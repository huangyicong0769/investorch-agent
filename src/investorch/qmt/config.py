from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

from investorch.config import AppConfig, ConfigError
from investorch.mcp import expand_mcp_variables, read_mcp_server_configs


@dataclass(frozen=True, slots=True)
class QMTConnectionProfile:
    server_name: str
    mcp_url: str
    rest_base_url: str
    headers: Mapping[str, str] = field(repr=False)
    timeout_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


def resolve_qmt_connection_profile(config: AppConfig) -> QMTConnectionProfile | None:
    """Resolve the configured MCP reference without attempting a network connection."""
    name = config["qmt.mcp_server"]
    if not name:
        return None
    try:
        servers = read_mcp_server_configs(config.mcp_config_path)
        raw = next((server for server in servers if server["name"] == name), None)
        if raw is None:
            raise ConfigError(f"qmt.mcp_server references unknown MCP server: {name}")
        if not raw.get("enabled", True):
            raise ConfigError(f"qmt.mcp_server references disabled MCP server: {name}")
        server = expand_mcp_variables(raw, config.secrets)
        url = urlsplit(server["url"])
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.path not in {"/mcp", "/mcp/"}
            or url.query
            or url.fragment
            or url.username is not None
            or url.password is not None
        ):
            raise ConfigError("QMT MCP URL must be an http(s) origin with path /mcp and no credentials/query/fragment")
        _ = url.port
        return QMTConnectionProfile(
            server_name=name,
            mcp_url=server["url"],
            rest_base_url=urlunsplit((url.scheme, url.netloc, "/api/v1", "", "")),
            headers=server.get("headers", {}),
            timeout_seconds=float(server.get("timeout", config["mcp.default_timeout_seconds"])),
        )
    except ConfigError:
        raise
    except (ValueError, TypeError, NotImplementedError) as exc:
        raise ConfigError(f"Invalid QMT MCP connection configuration for {name}") from exc
