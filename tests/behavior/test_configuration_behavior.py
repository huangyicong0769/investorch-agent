from __future__ import annotations

from pathlib import Path

import pytest

from investorch.config import PROJECT_CONFIG_PATH, REDACTED, ConfigError, load_config
from tests.support.config import make_test_config


def test_local_override_preserves_unset_defaults_and_project_root(tmp_path: Path) -> None:
    config = make_test_config(
        tmp_path,
        {"interaction": {"follow_up_behavior": "queue"}},
    )

    assert config["interaction.follow_up_behavior"] == "queue"
    assert config["runtime.max_turns"] == 100
    assert config.root == (tmp_path / "root").resolve()


def test_investorch_default_filesystem_identity(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)

    assert PROJECT_CONFIG_PATH.name == "investorch.toml"
    assert config.root_config_path == config.root / "investorch.toml"
    assert config.state_dir == config.root / "state"
    assert config.log_path == config.root / "state" / "logs" / "investorch.log"
    assert config.background_job_dir == config.root / "workspace" / ".investorch-processes"


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
