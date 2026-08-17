from typing import Any

from agents import RunContextWrapper
from agents.decorators import tool
from pydantic import BaseModel

from qmt_agent.context import AgentContext
from qmt_agent.mcp import (
    configure_mcp_server_config,
    read_mcp_server_configs,
    remove_mcp_server_config,
)


class MCPHeader(BaseModel):
    name : str
    value : str

@tool
def list_mcp_servers(context: RunContextWrapper[AgentContext],) -> dict[str, Any]:
    """
    List configured MCP servers.

    Secret placeholders are returned without
    expanding their real values.

    Returns:
        A dictionary with the list of configured MCP servers.
    """
    config = context.context.config

    return {
        "servers": read_mcp_server_configs(config.mcp_config_path),
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

    Changes are persisted to the local MCP configuration and take effect after QMT Agent is restarted.

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

    header_dict = (
        { header.name: header.value for header in headers }
        if headers is not None else None
    )

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
        "server": server,
        "persisted": True,
        "requires_restart": True,
    }


@tool(needs_approval=True)
def remove_mcp_server(context: RunContextWrapper[AgentContext], name: str) -> dict[str, Any]:
    """
    Remove an MCP server.

    This operation requires user approval.
    The change takes effect after QMT Agent is restarted.

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