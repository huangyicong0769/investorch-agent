from __future__ import annotations

from pathlib import Path

import pytest

from qmt_agent.config import ConfigError
from qmt_agent.initializer import initialize, sync_bootstrap_files
from tests.support.config import make_test_config


def test_first_initialization_creates_the_user_instance(tmp_path: Path) -> None:
    config = make_test_config(tmp_path, initialize_state=False)

    created = initialize(config)

    assert created is True
    assert config.root_config_path.is_file()
    assert config.mcp_config_path.is_file()
    assert config.workspace_dir.is_dir()
    assert config.state_dir.is_dir()
    assert config.sessions_db.is_file()
    assert (config.workspace_dir / "MEMORY.md").is_file()
    assert (config.workspace_dir / "memory" / "configuration.md").is_file()
    assert (config.workspace_dir / "memory" / "rqalpha.md").is_file()


def test_reinitialization_preserves_user_bootstrap_content(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    memory_path = config.workspace_dir / "MEMORY.md"
    user_content = "user-owned memory\n"
    memory_path.write_text(user_content, encoding="utf-8")

    created = initialize(config)

    assert created is False
    assert memory_path.read_text(encoding="utf-8") == user_content


@pytest.mark.asyncio
async def test_force_sync_replaces_user_content_and_keeps_a_recoverable_backup(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    template_path, memory_path = config.bootstrap_files[0]
    user_content = b"user-owned memory\n"
    memory_path.write_bytes(user_content)

    result = await sync_bootstrap_files(config, force=True)

    assert result.updated == 1
    assert memory_path.read_bytes() == template_path.read_bytes()
    assert result.backup_dir is not None
    assert (result.backup_dir / "MEMORY.md").read_bytes() == user_content


@pytest.mark.asyncio
async def test_failed_model_assisted_sync_restores_the_user_file(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    memory_path = config.workspace_dir / "MEMORY.md"
    user_content = "user-owned memory\n"
    memory_path.write_text(user_content, encoding="utf-8")

    async def failing_merge(target: Path, _template: str, _existed: bool) -> None:
        target.write_text("partial replacement", encoding="utf-8")
        raise RuntimeError("controlled merge failure")

    with pytest.raises(ConfigError):
        await sync_bootstrap_files(config, merge=failing_merge)

    assert memory_path.read_text(encoding="utf-8") == user_content
