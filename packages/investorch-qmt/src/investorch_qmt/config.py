from __future__ import annotations

import tomllib
from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs

_ROOT_KEYS = {"server", "auth", "logging"}
_SECTION_KEYS = {
    "server": {"host", "port", "allowed_hosts"},
    "auth": {"token"},
    "logging": {"level", "max_bytes", "backup_count"},
}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class ConfigError(ValueError):
    """Configuration cannot produce a safe startup snapshot."""


@dataclass(frozen=True)
class AppPaths:
    root: Path
    config: Path
    log: Path


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    allowed_hosts: tuple[str, ...]


@dataclass(frozen=True)
class AuthConfig:
    token: str


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    max_bytes: int
    backup_count: int


@dataclass(frozen=True)
class QMTConfig:
    server: ServerConfig
    auth: AuthConfig
    logging: LoggingConfig


def default_paths(root: Path | None = None) -> AppPaths:
    resolved_root = root or PlatformDirs(appname="QMT", appauthor="InvestOrch", roaming=False).user_data_path
    return AppPaths(
        root=resolved_root,
        config=resolved_root / "investorch-qmt.toml",
        log=resolved_root / "logs" / "investorch-qmt.log",
    )


def load_config(path: Path | None = None) -> QMTConfig:
    config_path = path or default_paths().config
    if not config_path.is_file():
        raise ConfigError("InvestOrch QMT is not initialized. Run: investorch-qmt init")

    defaults = _read_bundled_defaults()
    local = _read_toml(config_path)
    _validate_document(local)

    effective = deepcopy(defaults)
    for section, values in local.items():
        effective.setdefault(section, {}).update(values)

    return _build_config(effective)


def _read_bundled_defaults() -> dict[str, Any]:
    resource = files("investorch_qmt.resources").joinpath("investorch-qmt.toml")
    try:
        document = tomllib.loads(resource.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - packaged invariant
        raise ConfigError(f"Bundled configuration is invalid: {exc}") from exc
    _validate_document(document, allowed_root_keys={"server", "logging"})
    return document


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration at {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc


def _validate_document(document: dict[str, Any], *, allowed_root_keys: set[str] = _ROOT_KEYS) -> None:
    unknown_root = set(document) - allowed_root_keys
    if unknown_root:
        raise ConfigError(f"Unknown configuration section: {sorted(unknown_root)[0]}")

    for section, values in document.items():
        if not isinstance(values, dict):
            raise ConfigError(f"Configuration section '{section}' must be a table")
        unknown = set(values) - _SECTION_KEYS[section]
        if unknown:
            raise ConfigError(f"Unknown configuration key: {section}.{sorted(unknown)[0]}")


def _build_config(document: dict[str, Any]) -> QMTConfig:
    server = document.get("server", {})
    auth = document.get("auth", {})
    logging = document.get("logging", {})

    host = _nonempty_string(server.get("host"), "server.host")
    port = _integer(server.get("port"), "server.port", minimum=1, maximum=65_535)
    allowed_hosts = _string_list(server.get("allowed_hosts"), "server.allowed_hosts")
    if any("*" in allowed_host for allowed_host in allowed_hosts):
        raise ConfigError("server.allowed_hosts does not support wildcards")
    if host not in _LOOPBACK_HOSTS and not allowed_hosts:
        raise ConfigError("server.allowed_hosts is required when server.host is not loopback")

    token = _nonempty_string(auth.get("token"), "auth.token")
    if len(token) < 32 or any(not 0x21 <= ord(character) <= 0x7E for character in token):
        raise ConfigError("auth.token must contain at least 32 visible ASCII characters")

    level = _nonempty_string(logging.get("level"), "logging.level").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of: {', '.join(sorted(_LOG_LEVELS))}")

    return QMTConfig(
        server=ServerConfig(host=host, port=port, allowed_hosts=allowed_hosts),
        auth=AuthConfig(token=token),
        logging=LoggingConfig(
            level=level,
            max_bytes=_integer(logging.get("max_bytes"), "logging.max_bytes", minimum=1),
            backup_count=_integer(logging.get("backup_count"), "logging.backup_count", minimum=0),
        ),
    )


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        expected = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise ConfigError(f"{name} must be {expected}")
    return value


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ConfigError(f"{name} must be a list of non-empty strings")
    return tuple(value)
