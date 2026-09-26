from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

from investorch.config import AppConfig, ConfigError
from investorch.portfolio import init_portfolio_storage
from investorch.storage import init_session_metadata

LOCAL_CONFIG_TEMPLATE = """# Local InvestOrch Agent configuration.
# Overrides bundled InvestOrch Agent defaults and stores local secrets.

[secrets]
"""

MCP_CONFIG_TEMPLATE = """# Local MCP server configuration.
"""


def initialize(config: AppConfig) -> bool:
    """
    Initialize persistent local InvestOrch Agent state.

    Returns True when root/investorch.toml is created, so the caller can ask the user to configure required secrets before continuing.
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
    _ensure_directory(config.log_dir, name="logs", private=True)
    _ensure_directory(config.session_journal_dir, name="session journals", private=True)

    # Session schema initialization is idempotent.
    init_session_metadata(config.sessions_db)
    init_portfolio_storage(config.portfolio_db)

    memory_template = files("investorch.resources").joinpath("MEMORY.md.template").read_text(encoding="utf-8")
    _ensure_file(config.workspace_dir / "MEMORY.md", memory_template)

    return root_config_created


def _ensure_directory(path: Path, *, name: str, private: bool = False) -> None:
    if path.exists():
        if not path.is_dir():
            raise ConfigError(f"{name} is not a directory: {path}")
    else:
        path.mkdir(parents=True, exist_ok=True)

    if private and os.name == "posix":
        path.chmod(0o700)


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
