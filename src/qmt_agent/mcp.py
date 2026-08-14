import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.mcp import MCPServer, MCPServerStreamableHttp

_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

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

def load_mcp_servers(path: str | Path, variables: Mapping[str, str] | None = None) -> list[MCPServer]:

    with Path(path).open("rb") as file:
        config = tomllib.load(file)

    servers: list[MCPServer] = []

    for raw_server in config.get("servers", []):
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
            )
        )

    return servers