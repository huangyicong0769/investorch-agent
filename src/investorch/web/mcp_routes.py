"""HTTP adapters for the persisted, user-managed MCP server registry."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StrictBool, StrictFloat, StrictInt, StrictStr

from investorch.mcp import (
    SUPPORTED_MCP_TRANSPORTS,
    MCPServerExistsError,
    MCPServerNotFoundError,
    configure_mcp_server_config,
    public_mcp_server_config,
    read_mcp_server_configs,
    remove_mcp_server_config,
)

from .errors import APIError
from .routes import ConfirmRequest, Host

router = APIRouter(prefix="/api/integrations/mcp")


class UpdateMCPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: StrictStr | None = None
    enabled: StrictBool | None = None
    cache_tools_list: StrictBool | None = None
    headers: dict[StrictStr, StrictStr] | None = None
    timeout: StrictInt | StrictFloat | None = None
    transport: StrictStr | None = None


class CreateMCPRequest(UpdateMCPRequest):
    name: StrictStr


def _public_server(server: dict[str, Any]) -> dict[str, Any]:
    return {**public_mcp_server_config(server), "valid": True, "runtime_status": "unknown"}


def _raise_mcp_error(error: Exception) -> NoReturn:
    if isinstance(error, MCPServerExistsError):
        raise APIError(409, "mcp_server_exists", "An MCP server with this name already exists.") from None
    if isinstance(error, MCPServerNotFoundError):
        raise APIError(404, "mcp_server_not_found", "The requested MCP server does not exist.") from None
    if isinstance(error, OSError):
        raise APIError(503, "mcp_storage_error", "The MCP configuration file could not be accessed.") from None
    if isinstance(error, NotImplementedError):
        raise APIError(422, "unsupported_mcp_transport", "Only streamable_http MCP transport is supported.") from None
    raise APIError(
        422,
        "invalid_mcp_configuration",
        "Invalid MCP configuration. Check the server name, URL, headers and positive finite timeout in mcp.toml.",
    ) from None


@router.get("")
def list_servers(host: Host) -> dict[str, Any]:
    try:
        return {
            "servers": [_public_server(server) for server in read_mcp_server_configs(host.config.mcp_config_path)],
            "supported_transports": list(SUPPORTED_MCP_TRANSPORTS),
        }
    except (OSError, ValueError, TypeError, NotImplementedError) as error:
        _raise_mcp_error(error)


@router.post("", status_code=201)
def create_server(request: CreateMCPRequest, host: Host) -> dict[str, Any]:
    try:
        server = configure_mcp_server_config(
            host.config.mcp_config_path, **request.model_dump(exclude_none=True), operation="create"
        )
    except (OSError, ValueError, TypeError, NotImplementedError) as error:
        _raise_mcp_error(error)
    return {"server": _public_server(server), "persisted": True, "requires_restart": True}


@router.patch("/{name}")
def update_server(name: str, request: UpdateMCPRequest, host: Host) -> dict[str, Any]:
    try:
        server = configure_mcp_server_config(
            host.config.mcp_config_path, name, **request.model_dump(exclude_none=True), operation="update"
        )
    except (OSError, ValueError, TypeError, NotImplementedError) as error:
        _raise_mcp_error(error)
    return {"server": _public_server(server), "persisted": True, "requires_restart": True}


@router.delete("/{name}")
def delete_server(name: str, request: ConfirmRequest, host: Host) -> dict[str, Any]:
    if not request.confirm:
        raise APIError(400, "confirmation_required", "Explicit confirmation is required to remove an MCP server.")
    try:
        removed = remove_mcp_server_config(host.config.mcp_config_path, name)
    except (OSError, ValueError, TypeError, NotImplementedError) as error:
        _raise_mcp_error(error)
    if not removed:
        raise APIError(404, "mcp_server_not_found", "The requested MCP server does not exist.")
    return {"name": name, "removed": True, "persisted": True, "requires_restart": True}
