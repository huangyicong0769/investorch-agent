import re
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlsplit, urlunsplit

from agents import RunContextWrapper
from agents.decorators import tool
from pydantic import BaseModel

from investorch.config import REDACTED
from investorch.context import AgentContext
from investorch.mcp import (
    configure_mcp_server_config,
    read_mcp_server_configs,
    remove_mcp_server_config,
)

_SECRET_PLACEHOLDER_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


class MCPHeader(BaseModel):
    name: str
    value: str


@tool
def list_mcp_servers(
    context: RunContextWrapper[AgentContext],
) -> dict[str, Any]:
    """
    List user-configured MCP servers from mcp.toml.

    Built-in runtime MCP servers are outside this configuration scope. Secret placeholders are returned without expanding their real values, while literal URL query and header values are redacted.

    Returns:
        A scoped dictionary with the list of user-configured MCP servers.
    """
    config = context.context.config

    return {
        "servers": [_public_mcp_server_config(server) for server in read_mcp_server_configs(config.mcp_config_path)],
    }


@tool(needs_approval=True)
def configure_mcp_server(
    context: RunContextWrapper[AgentContext],
    name: str,
    url: str | None = None,
    enabled: bool | None = None,
    cache_tools_list: bool | None = None,
    headers: list[MCPHeader] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """
    Add or update an MCP server.

    This operation requires user approval.
    Only streamable HTTP MCP servers are currently supported.
    For an existing server, omitted values keep their current values.
    When adding a new server, url is required.

    Secrets should be referenced in headers with
    ${SECRET_NAME}, not written directly.

    Changes are persisted to the local MCP configuration and take effect after InvestOrch Agent is restarted.

    Args:
        name: The name of the MCP server.
        url: The URL of the MCP server.
        enabled: Whether the server is enabled.
        cache_tools_list: Whether to cache the tools list from this server.
        headers: Optional headers as name/value pairs to send with requests to this server.
            Secrets should be referenced with ${SECRET_NAME}, not written directly.
        timeout: Optional timeout for requests to this server, in seconds.

    Returns:
        A dictionary with the updated server configuration.
    """
    config = context.context.config

    header_dict = {header.name: header.value for header in headers} if headers is not None else None

    server = configure_mcp_server_config(
        config.mcp_config_path,
        name,
        url=url,
        enabled=enabled,
        cache_tools_list=cache_tools_list,
        headers=header_dict,
        timeout=timeout,
    )

    return {
        "server": _public_mcp_server_config(server),
        "persisted": True,
        "requires_restart": True,
    }


@tool(needs_approval=True)
def remove_mcp_server(context: RunContextWrapper[AgentContext], name: str) -> dict[str, Any]:
    """
    Remove an MCP server.

    This operation requires user approval.
    The change takes effect after InvestOrch Agent is restarted.

    Args:
        name: The name of the MCP server to remove.

    Returns:
        A dictionary indicating whether the server was removed.
    """
    config = context.context.config

    removed = remove_mcp_server_config(
        config.mcp_config_path,
        name,
    )

    return {
        "name": name,
        "removed": removed,
        "requires_restart": removed,
    }


def _public_mcp_server_config(server: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(server)

    if isinstance(public.get("url"), str):
        public["url"] = _redact_url(public["url"])

    if isinstance(public.get("headers"), dict):
        public["headers"] = {name: _public_secret_value(value) for name, value in public["headers"].items()}

    return public


def _public_secret_value(value: str) -> str:
    return value if _SECRET_PLACEHOLDER_PATTERN.fullmatch(value) else REDACTED


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = re.sub(r"^[^@]+@", f"{REDACTED}@", parts.netloc)
    query = "&".join(
        f"{quote_plus(name)}={_public_secret_value(value)}"
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    )
    fragment = _public_secret_value(parts.fragment) if parts.fragment else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
