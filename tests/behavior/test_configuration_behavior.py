from __future__ import annotations

from pathlib import Path

import pytest

from investorch.cli import parse_web_args
from investorch.config import REDACTED, ConfigError, load_config
from tests.support.config import make_test_config


def test_local_override_preserves_unset_defaults_and_project_root(tmp_path: Path) -> None:
    baseline = make_test_config(tmp_path / "baseline")
    config = make_test_config(
        tmp_path / "override",
        {"interaction": {"follow_up_behavior": "queue"}},
    )

    assert config["interaction.follow_up_behavior"] == "queue"
    assert config["runtime.max_turns"] == baseline["runtime.max_turns"]
    assert config.root == (tmp_path / "override" / "root").resolve()


def test_portfolio_database_follows_custom_state_directory(tmp_path: Path) -> None:
    config = make_test_config(tmp_path, {"paths": {"state": "custom-state"}})

    assert config.portfolio_db == config.state_dir / "portfolio.db"


def test_web_cli_port_override_is_optional() -> None:
    assert parse_web_args([]).port is None
    assert parse_web_args(["--port", "8000"]).port == 8000


def test_web_updates_require_restart(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)

    with pytest.raises(ConfigError, match=r"web\.default_port requires persist=true"):
        config.update("web.default_port", 8000, persist=False)


def test_local_config_cannot_redirect_project_root(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    config.root_config_path.write_text(
        f'[paths]\nroot = "{tmp_path / "elsewhere"}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"paths\.root"):
        load_config(config.project_config_path)


def test_local_config_cannot_define_bootstrap_policy(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    config.root_config_path.write_text("[bootstrap]\nfiles = []\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="bootstrap"):
        load_config(config.project_config_path)


def test_public_config_redacts_secrets_without_breaking_secret_lookup(tmp_path: Path) -> None:
    secret = "super-secret"
    config = make_test_config(tmp_path, {"secrets": {"TEST_SECRET": secret}})

    assert config.secret("TEST_SECRET") == secret
    assert config.get("secrets.TEST_SECRET") == REDACTED
    assert config.public()["secrets"]["TEST_SECRET"] == REDACTED
    assert secret not in repr(config.public())


def test_hot_update_applies_immediately_and_persists(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)

    result = config.update("interaction.follow_up_behavior", "queue", persist=True)

    assert result == {
        "key": "interaction.follow_up_behavior",
        "value": "queue",
        "persisted": True,
        "applied": True,
        "requires_restart": False,
    }
    assert config["interaction.follow_up_behavior"] == "queue"
    assert load_config(config.project_config_path)["interaction.follow_up_behavior"] == "queue"


def test_restart_required_update_persists_without_mutating_current_config(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    original = config["logging.level"]

    result = config.update("logging.level", "DEBUG", persist=True)

    assert result["persisted"] is True
    assert result["requires_restart"] is True
    assert result["applied"] is False
    assert config["logging.level"] == original
    assert load_config(config.project_config_path)["logging.level"] == "DEBUG"


def test_invalid_update_leaves_memory_and_disk_unchanged(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    original_value = config["interaction.follow_up_behavior"]
    original_file = config.root_config_path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError):
        config.update("interaction.follow_up_behavior", "invalid", persist=True)

    assert config["interaction.follow_up_behavior"] == original_value
    assert config.root_config_path.read_text(encoding="utf-8") == original_file
    assert load_config(config.project_config_path)["interaction.follow_up_behavior"] == original_value


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("web.default_port", 65536, r"web\.default_port"),
        (
            "web.websocket_reconnect_max_delay_ms",
            100,
            r"web\.websocket_reconnect_max_delay_ms",
        ),
        ("tui.todo_contents_max_height", 7, r"tui\.todo_contents_max_height"),
    ],
)
def test_invalid_web_and_tui_policy_is_rejected(tmp_path: Path, key: str, value: int, message: str) -> None:
    config = make_test_config(tmp_path)

    with pytest.raises(ConfigError, match=message):
        config.update(key, value, persist=True)
