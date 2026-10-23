"""Keep individual MCP transport failures inside their connection boundary."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
import httpx2
from agents import RunContextWrapper, UserError
from agents.exceptions import AgentsException
from agents.mcp import MCPServerStdio, MCPServerStreamableHttp
from agents.tool import default_tool_error_function
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT

from investorch.context import AgentContext

logger = logging.getLogger(__name__)


def _error_leaves(error: BaseException):
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            yield from _error_leaves(nested)
    else:
        yield error


def _connection_failure(error: Exception) -> bool:
    if isinstance(error, ExceptionGroup):
        return all(_connection_failure(nested) for nested in error.exceptions)
    if isinstance(error, MCPError):
        return error.code in {CONNECTION_CLOSED, REQUEST_TIMEOUT}
    if isinstance(
        error,
        (
            httpx.HTTPError,
            httpx2.HTTPError,
            ConnectionError,
            TimeoutError,
            BrokenResourceError,
            ClosedResourceError,
            EndOfStream,
        ),
    ):
        return True
    if isinstance(error, UserError):
        message = str(error)
        return message == "Server not initialized. Make sure you call `connect()` first." or (
            "MCP server '" in message
            and message.startswith(("Failed to connect", "Failed to list tools", "Failed to call tool"))
            and any(
                detail in message
                for detail in (
                    "HTTP error",
                    "Connection lost.",
                    "Connection timeout.",
                    "Could not reach the server.",
                    "Request failed.",
                )
            )
        )
    return False


class _FailureIsolation:
    def __init__(self, *args: Any, diagnostic_secrets: tuple[str, ...] = (), **kwargs: Any):
        kwargs.setdefault("failure_error_function", self._tool_error)
        super().__init__(*args, **kwargs)
        self.runtime_status = "unknown"
        self.runtime_error: dict[str, str] | None = None
        self._diagnostic_secrets = diagnostic_secrets

    def _failed(self, operation: str, error: BaseException) -> str:
        from investorch.mcp import _redact_url

        detail = "; ".join(f"{type(nested).__name__}: {nested}" for nested in _error_leaves(error))
        for secret in sorted(self._diagnostic_secrets, key=len, reverse=True):
            if secret:
                detail = detail.replace(secret, "<redacted>")
        detail = re.sub(r"https?://[^\s\"'<>]+", lambda match: _redact_url(match[0]), detail)
        detail = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", detail)
        self.runtime_status = "unavailable"
        self.runtime_error = {"operation": operation, "message": detail}
        self.invalidate_tools_cache()
        logger.warning("MCP server %s unavailable during %s: %s", self.name, operation, detail)
        return detail

    def _available(self):
        self.runtime_status = "available"
        self.runtime_error = None

    def record_connection_result(self, error: BaseException | None) -> None:
        """Observe the SDK manager's result, including its outer connect timeout."""
        if error is not None:
            self._failed("connect", error)
        else:
            self.runtime_status = "connected"
            self.runtime_error = None

    async def list_tools(self, run_context: RunContextWrapper[Any] | None = None, agent=None):
        context = run_context.context if run_context is not None else None
        failures = context.turn.mcp_errors if isinstance(context, AgentContext) else None
        if failures is not None and self.name in failures:
            return []
        if self.session is None and self.runtime_error is not None:
            if failures is not None:
                failures[self.name] = dict(self.runtime_error)
            return []
        try:
            tools = await super().list_tools(run_context, agent)
        except Exception as error:
            if not _connection_failure(error):
                raise
            self._failed("list_tools", error)
            if failures is not None:
                failures[self.name] = dict(self.runtime_error)
            return []
        self._available()
        return tools

    def _tool_error(self, context: RunContextWrapper[Any], error: Exception) -> str:
        cause = error.__cause__ if isinstance(error, AgentsException) and error.__cause__ is not None else error
        if not _connection_failure(cause):
            return default_tool_error_function(context, error)
        detail = self._failed("call_tool", cause)
        if isinstance(context.context, AgentContext):
            context.context.turn.mcp_errors[self.name] = dict(self.runtime_error)
        return (
            f"MCP server {self.name!r}: {detail}\n"
            "The remote operation's outcome is unknown. Verify its status before another attempt. "
            "Work using available tools can continue."
        )


class IsolatedMCPServerStreamableHttp(_FailureIsolation, MCPServerStreamableHttp):
    """Streamable HTTP server with per-connection failure isolation."""


class IsolatedMCPServerStdio(_FailureIsolation, MCPServerStdio):
    """Built-in stdio server with the same failure boundary."""
