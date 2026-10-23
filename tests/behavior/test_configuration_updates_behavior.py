from pathlib import Path

import pytest

from investorch.config import ConfigError, load_config


def test_loading_incomplete_model_configuration_preserves_file_and_requires_setup(tmp_path: Path) -> None:
    local = tmp_path / "investorch.toml"
    original = b'[secrets]\nPROVIDER_KEY = "existing-test-key"\n[agents.main]\nname = "explicit-model" # user choice\n'
    local.write_bytes(original)
    config = load_config(root=tmp_path)
    assert "agents.main.model" in config.readiness()
    assert config["agents.main.name"] == "explicit-model"
    assert config.secret("PROVIDER_KEY") == "existing-test-key"
    assert local.read_bytes() == original
    assert list(tmp_path.iterdir()) == [local]


def test_bad_config_never_gets_replaced_or_backed_up(tmp_path: Path) -> None:
    local = tmp_path / "investorch.toml"
    original = b"[broken TOML"
    local.write_bytes(original)
    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(root=tmp_path)
    assert local.read_bytes() == original
    assert list(tmp_path.iterdir()) == [local]


@pytest.mark.parametrize("symlink", [False, True])
def test_loading_config_rejects_workspace_escape_without_changing_files(tmp_path: Path, symlink: bool) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("existing workspace data")
    if symlink:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    local = root / "investorch.toml"
    original = f'[paths]\nworkspace = "{"linked" if symlink else "../outside"}"\n'.encode()
    local.write_bytes(original)
    with pytest.raises(ConfigError, match=r"escapes paths\.root"):
        load_config(root=root)
    assert local.read_bytes() == original
    assert marker.read_text() == "existing workspace data"


def test_batch_update_is_atomic_and_restart_values_wait_until_reload(tmp_path: Path) -> None:
    config = load_config(root=tmp_path)
    config.ensure_local_file()
    original = config.root_config_path.read_bytes()
    original_turns = config["runtime.max_turns"]
    with pytest.raises(ConfigError):
        config.update_many({"runtime.max_turns": 80, "web.default_port": 70000})
    assert config.root_config_path.read_bytes() == original
    assert config["runtime.max_turns"] == original_turns

    original_port = config["web.default_port"]
    result = config.update_many({"runtime.max_turns": 80, "web.default_port": 8123})
    assert result["fields"]["runtime.max_turns"]["applied"] is True
    assert result["fields"]["web.default_port"]["requires_restart"] is True
    assert config["runtime.max_turns"] == 80
    assert config["web.default_port"] == original_port
    assert config.public(saved=True)["web"]["default_port"] == 8123
    reloaded = load_config(root=tmp_path)
    assert reloaded["runtime.max_turns"] == 80
    assert reloaded["web.default_port"] == 8123


@pytest.mark.parametrize(
    "changes",
    [
        {"session_tools.default_page_size": 30, "session_tools.max_page_size": 20},
        {"session_tools.max_field_chars": 2000, "session_tools.max_response_chars": 1000},
        {"portfolio_tools.default_ledger_limit": 30, "portfolio_tools.max_ledger_limit": 20},
    ],
)
def test_invalid_tool_reading_budgets_preserve_effective_values_and_local_file(
    tmp_path: Path, changes: dict[str, int]
) -> None:
    config = load_config(root=tmp_path)
    config.ensure_local_file()
    before = config.public()
    original = config.root_config_path.read_bytes()

    with pytest.raises(ConfigError):
        config.update_many(changes)

    assert config.public() == before
    assert config.root_config_path.read_bytes() == original


def test_reset_removes_override_and_preserves_comments_secrets_and_snapshot(tmp_path: Path) -> None:
    original_turns = load_config(root=tmp_path)["runtime.max_turns"]
    local = tmp_path / "investorch.toml"
    local.write_text('# User note\n[runtime]\nmax_turns = 25\n[secrets]\nKEY = "sensitive-test-token"\n')
    config = load_config(root=tmp_path)
    snapshot = config.snapshot()
    config.reset(["runtime.max_turns"])
    assert snapshot["runtime.max_turns"] == 25
    assert config["runtime.max_turns"] == original_turns
    assert "runtime.max_turns" not in config.override_keys()
    assert b"# User note" in local.read_bytes()
    assert config.secret("KEY") == "sensitive-test-token"
    assert "sensitive-test-token" not in repr(config.public())


def test_replacing_credentials_reports_restart_and_retains_active_key(tmp_path: Path) -> None:
    local = tmp_path / "investorch.toml"
    local.write_text('[secrets]\nKEY = "original-test-key"\n')
    config = load_config(root=tmp_path)
    result = config.update_many({"secrets.KEY": "replacement-test-key"})
    assert result["requires_restart"] is True
    assert result["fields"]["secrets.KEY"]["applied"] is False
    assert config.secret("KEY") == "original-test-key"
    assert load_config(root=tmp_path).secret("KEY") == "replacement-test-key"
    with pytest.raises(ConfigError):
        config.update_many({"secrets.KEY": "temporary-test-key"}, persist=False)


def test_external_edit_is_not_silently_overwritten(tmp_path: Path) -> None:
    config = load_config(root=tmp_path)
    config.ensure_local_file()
    config.root_config_path.write_text("[runtime]\nmax_turns = 12\n")
    before = config.root_config_path.read_bytes()
    original_turns = config["runtime.max_turns"]
    with pytest.raises(ConfigError, match="changed outside"):
        config.update("runtime.max_turns", 15)
    assert config.root_config_path.read_bytes() == before
    assert config["runtime.max_turns"] == original_turns


def test_failed_atomic_replace_leaves_file_memory_and_no_incomplete_output(tmp_path: Path, monkeypatch) -> None:
    import os

    config = load_config(root=tmp_path)
    config.ensure_local_file()
    original = config.root_config_path.read_bytes()
    original_turns = config["runtime.max_turns"]

    def fail_replace(_source, _target):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="filesystem failure"):
        config.update_many({"runtime.max_turns": 12, "secrets.NEW_KEY": "private-value"})
    assert config.root_config_path.read_bytes() == original
    assert config["runtime.max_turns"] == original_turns
    assert "NEW_KEY" not in config.secret_status()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["investorch.toml"]
