"""Keep individual MCP transport failures inside their connection boundary."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
import httpx2
from agents import RunContextWrapper, UserError
from agents.mcp import MCPServerStdio, MCPServerStreamableHttp
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT, CallToolResult, TextContent

from investorch.context import AgentContext

logger = logging.getLogger(__name__)


def _connection_failure(error: Exception) -> bool:
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
        super().__init__(*args, **kwargs)
        self.runtime_status = "unknown"
        self.runtime_error: dict[str, str] | None = None
        self._diagnostic_secrets = diagnostic_secrets

    def _failed(self, operation: str, error: Exception) -> str:
        from investorch.mcp import _redact_url

        detail = f"{type(error).__name__}: {error}"
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

    async def connect(self):
        try:
            await super().connect()
        except Exception as error:
            if _connection_failure(error):
                self._failed("connect", error)
            raise
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

    async def call_tool(self, tool_name, arguments, meta=None):
        try:
            result = await super().call_tool(tool_name, arguments, meta=meta)
        except Exception as error:
            if not _connection_failure(error):
                raise
            detail = self._failed("call_tool", error)
            return CallToolResult(
                isError=True,
                content=[
                    TextContent(
                        type="text",
                        text=(
                            f"MCP server {self.name!r}, tool {tool_name!r}: {detail}\n"
                            "The remote operation's outcome is unknown. Verify its status before another attempt. "
                            "Work using available tools can continue."
                        ),
                    )
                ],
            )
        self._available()
        return result


class IsolatedMCPServerStreamableHttp(_FailureIsolation, MCPServerStreamableHttp):
    """Streamable HTTP server with per-connection failure isolation."""


class IsolatedMCPServerStdio(_FailureIsolation, MCPServerStdio):
    """Built-in stdio server with the same failure boundary."""
