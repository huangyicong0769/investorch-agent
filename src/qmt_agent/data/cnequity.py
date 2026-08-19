"""Transitional backend for the managed curated-data bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

from agents.mcp import MCPServer, MCPServerStdio
from cnequity.config import Config, load_config, validate_config, write_user_config
from cnequity.storage.layout import init_data_layout

from qmt_agent.config import ConfigError

CONFIG_FILENAME = "cnequity.toml"
DATA_SUBDIRECTORY = "cnequity"


def initialize(managed_data_dir: Path, config_root: Path) -> None:
    """Bootstrap the backend config and local layout without ingestion."""
    managed_data_dir = managed_data_dir.expanduser().resolve()
    config_root = config_root.expanduser().resolve()
    if managed_data_dir.exists() and not managed_data_dir.is_dir():
        raise ConfigError(f"data is not a directory: {managed_data_dir}")

    config_path = config_root / CONFIG_FILENAME

    if not config_path.exists():
        try:
            write_user_config(config_path, data_root=str(managed_data_dir / DATA_SUBDIRECTORY), force=False)
        except FileExistsError:
            pass
        except Exception as exc:
            raise ConfigError(f"Unable to create managed data configuration: {config_path}: {exc}") from exc

    config = _load_and_validate(config_path)

    try:
        init_data_layout(config)
    except Exception as exc:
        raise ConfigError(f"Unable to initialize managed data layout: {exc}") from exc


def load_query_servers(config_root: Path, timeout_seconds: int | float) -> list[MCPServer]:
    """Build the read-only stdio query server without probing lake readiness."""
    config_root = config_root.expanduser().resolve()
    config_path = (config_root / CONFIG_FILENAME).resolve()
    params = {
        "command": sys.executable,
        "args": ["-m", "cnequity", "mcp", "--config", str(config_path)],
        "cwd": str(config_root),
    }
    return [
        MCPServerStdio(
            params=params,
            name="Managed market-data query",
            cache_tools_list=True,
            client_session_timeout_seconds=timeout_seconds,
        )
    ]


def _load_and_validate(config_path: Path) -> Config:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise ConfigError(f"Unable to load managed data configuration {config_path}: {exc}") from exc

    try:
        errors = validate_config(config)
    except Exception as exc:
        raise ConfigError(f"Unable to validate managed data configuration {config_path}: {exc}") from exc

    if errors:
        joined = "; ".join(str(error) for error in errors)
        raise ConfigError(f"Managed data configuration is invalid: {joined}")

    return config
