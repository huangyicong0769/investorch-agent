import os
import re
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import tomlkit
from agents.mcp import MCPServer, MCPServerStreamableHttp

_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def read_mcp_server_configs(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)

    if not path.exists():
        return []

    try:
        with path.open("rb") as file:
            config = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid MCP TOML in {path}: {exc}") from exc

    raw_servers = config.get("servers", [])

    if not isinstance(raw_servers, list):
        raise TypeError("MCP config 'servers' must be an array of tables")

    servers: list[dict[str, Any]] = []
    names: set[str] = set()

    for raw_server in raw_servers:
        if not isinstance(raw_server, dict):
            raise TypeError("Each MCP server must be a table")

        server = deepcopy(raw_server)
        _validate_server_config(server)

        name = server["name"]

        if name in names:
            raise ValueError(f"Duplicate MCP server name: {name}")

        names.add(name)
        servers.append(server)

    return servers


def configure_mcp_server_config(
    path: str | Path,
    name: str,
    *,
    url: str | None = None,
    enabled: bool | None = None,
    cache_tools_list: bool | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    name = name.strip()

    if not name:
        raise ValueError("MCP server name cannot be empty")

    servers = read_mcp_server_configs(path)

    server_index: int | None = None

    for index, server in enumerate(servers):
        if server["name"] == name:
            server_index = index
            break

    if server_index is None:
        if url is None:
            raise ValueError("url is required when adding a new MCP server")

        server: dict[str, Any] = {
            "name": name,
            "enabled": True,
            "transport": "streamable_http",
            "url": url,
            "cache_tools_list": False,
        }
    else:
        server = deepcopy(servers[server_index])

    if url is not None:
        server["url"] = url

    if enabled is not None:
        server["enabled"] = enabled

    if cache_tools_list is not None:
        server["cache_tools_list"] = cache_tools_list

    if headers is not None:
        if headers:
            server["headers"] = dict(headers)
        else:
            server.pop("headers", None)

    if timeout is not None:
        server["timeout"] = timeout

    _validate_server_config(server)

    if server_index is None:
        servers.append(server)
    else:
        servers[server_index] = server

    _write_mcp_server_configs(path, servers)

    return deepcopy(server)


def remove_mcp_server_config(path: str | Path, name: str) -> bool:
    servers = read_mcp_server_configs(path)

    remaining = [server for server in servers if server["name"] != name]

    if len(remaining) == len(servers):
        return False

    _write_mcp_server_configs(path, remaining)

    return True


def _expand_variables(value: Any, variables: Mapping[str, str]) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)

            try:
                return variables[name]
            except KeyError as exc:
                raise ValueError(f"Missing config secret '{name}'") from exc

        return _VARIABLE_PATTERN.sub(replace, value)

    if isinstance(value, list):
        return [_expand_variables(item, variables) for item in value]

    if isinstance(value, dict):
        return {key: _expand_variables(item, variables) for key, item in value.items()}

    return value


def load_mcp_servers(
    path: str | Path, variables: Mapping[str, str] | None, default_timeout_seconds: int | float
) -> list[MCPServer]:
    configs = read_mcp_server_configs(path)

    servers: list[MCPServer] = []

    for raw_server in configs:
        if not raw_server.get("enabled", True):
            continue

        server = _expand_variables(raw_server, variables or {})

        transport = server["transport"]

        if transport != "streamable_http":
            raise NotImplementedError(f"Unsupported MCP transport: {transport}")

        params: dict[str, Any] = {"url": server["url"]}

        if "headers" in server:
            params["headers"] = server["headers"]

        if "timeout" in server:
            params["timeout"] = server["timeout"]

        servers.append(
            MCPServerStreamableHttp(
                name=server["name"],
                params=params,
                cache_tools_list=server.get(
                    "cache_tools_list",
                    False,
                ),
                client_session_timeout_seconds=server.get("timeout", default_timeout_seconds),
            )
        )

    return servers


def _validate_server_config(server: dict[str, Any]) -> None:
    name = server.get("name")

    if not isinstance(name, str) or not name.strip():
        raise ValueError("MCP server name must be a non-empty string")

    transport = server.get("transport")

    if transport != "streamable_http":
        raise NotImplementedError("Only streamable_http MCP transport is supported")

    url = server.get("url")

    if not isinstance(url, str) or not url.strip():
        raise ValueError("MCP server url must be a non-empty string")

    enabled = server.get("enabled", True)

    if not isinstance(enabled, bool):
        raise TypeError("MCP server enabled must be a bool")

    cache_tools_list = server.get("cache_tools_list", False)

    if not isinstance(cache_tools_list, bool):
        raise TypeError("MCP server cache_tools_list must be a bool")

    headers = server.get("headers")

    if headers is not None:
        if not isinstance(headers, dict):
            raise TypeError("MCP server headers must be a table")

        if not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()):
            raise ValueError("MCP headers must contain string values")

    timeout = server.get("timeout")

    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ValueError("MCP timeout must be a positive number")


def _write_mcp_server_configs(path: str | Path, servers: list[dict[str, Any]]) -> None:
    path = Path(path)

    document = tomlkit.document()
    server_array = tomlkit.aot()

    for server in servers:
        table = tomlkit.table()

        for key in (
            "name",
            "enabled",
            "transport",
            "url",
            "cache_tools_list",
            "timeout",
        ):
            if key in server:
                table[key] = server[key]

        if "headers" in server:
            table["headers"] = server["headers"]

        server_array.append(table)

    document["servers"] = server_array

    path.write_text(
        tomlkit.dumps(document),
        encoding="utf-8",
    )

    if os.name == "posix":
        path.chmod(0o600)
