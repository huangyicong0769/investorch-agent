from __future__ import annotations

import os
from pathlib import Path

from qmt_agent.config import AppConfig, ConfigError
from qmt_agent.storage.sessions import init_session_metadata

LOCAL_CONFIG_TEMPLATE = """# Local QMT Agent configuration.
# Overrides config/qmt.toml and stores local secrets.

[secrets]
"""

MCP_CONFIG_TEMPLATE = """# Local MCP server configuration.
"""


def initialize(config: AppConfig) -> bool:
    """
    Initialize persistent local QMT Agent state.

    Returns True when root/qmt.toml is created, so the caller can ask the user to configure required secrets before continuing.
    """
    _ensure_directory(config.root, name="paths.root")

    root_config_created = _ensure_file(
        config.root_config_path,
        LOCAL_CONFIG_TEMPLATE,
        private=True,
    )

    _ensure_file(config.mcp_config_path, MCP_CONFIG_TEMPLATE, private=True)

    _ensure_directory(config.workspace_dir, name="workspace")

    _ensure_directory(config.state_dir, name="state")

    # Session schema initialization is idempotent.
    init_session_metadata(config.sessions_db)

    marker = config.state_dir / "initialized"

    if not marker.exists():
        _copy_bootstrap_files(config)
        marker.touch()

    return root_config_created


def _copy_bootstrap_files(config: AppConfig) -> None:
    for source, target in config.bootstrap_files:
        # Existing workspace state always belongs
        # to the user and must never be overwritten.
        if target.exists():
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        target.write_bytes(source.read_bytes())


def _ensure_directory(path: Path, *, name: str) -> None:
    if path.exists():
        if not path.is_dir():
            raise ConfigError(f"{name} is not a directory: {path}")

        return

    path.mkdir(parents=True, exist_ok=True)


def _ensure_file(
    path: Path,
    content: str,
    *,
    private: bool = False,
) -> bool:
    if path.exists():
        if not path.is_file():
            raise ConfigError(f"Expected a file: {path}")

        return False

    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding="utf-8")

    if private and os.name == "posix":
        path.chmod(0o600)

    return True