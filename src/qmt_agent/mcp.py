import os
import re
import tomllib
from pathlib import Path
from typing import Any

from agents.mcp import MCPServer, MCPServerStreamableHttp

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            return os.environ[name]

        return _ENV_PATTERN.sub(replace, value)

    if isinstance(value, list):
        return [_expand_env(item) for item in value]

    if isinstance(value, dict):
        return {
            key: _expand_env(item)
            for key, item in value.items()
        }

    return value

def load_mcp_servers(path: str | Path) -> list[MCPServer]:

    with Path(path).open("rb") as file:
        config = tomllib.load(file)

    servers: list[MCPServer] = []

    for raw_server in config.get("servers", []):
        server = _expand_env(raw_server)

        transport = server["transport"]

        if transport != "streamable_http":
            raise ValueError(
                f"Unsupported MCP transport: {transport}"
            )

        params: dict[str, Any] = {
            "url": server["url"],
        }

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