from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from investorch.initializer import initialize
from investorch.portfolio import Portfolio, create_portfolio, get_portfolio
from investorch.storage import create_session, delete_session_metadata
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
    assert config.portfolio_db.is_file()
    assert (config.workspace_dir / "MEMORY.md").is_file()
    assert not (config.workspace_dir / "memory" / "configuration.md").exists()
    assert not (config.workspace_dir / "memory" / "rqalpha.md").exists()


def test_reinitialization_preserves_user_memory_content(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    memory_path = config.workspace_dir / "MEMORY.md"
    user_content = "user-owned memory\n"
    memory_path.write_text(user_content, encoding="utf-8")

    created = initialize(config)

    assert created is False
    assert memory_path.read_text(encoding="utf-8") == user_content


def test_session_lifecycle_does_not_modify_portfolio_persistence(tmp_path: Path) -> None:
    config = make_test_config(tmp_path, {"paths": {"state": "custom-state"}})
    now = datetime(2026, 1, 1, tzinfo=UTC)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", now, now)
    create_portfolio(config.portfolio_db, portfolio)

    create_session(config.sessions_db, "session-1")
    delete_session_metadata(config.sessions_db, "session-1")

    assert get_portfolio(config.portfolio_db, portfolio.id) == portfolio


def test_reinitialization_preserves_legacy_workspace_guides(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    legacy = config.workspace_dir / "memory" / "configuration.md"
    legacy.parent.mkdir()
    legacy.write_text("User-owned legacy content", encoding="utf-8")

    initialize(config)

    assert legacy.read_text(encoding="utf-8") == "User-owned legacy content"
