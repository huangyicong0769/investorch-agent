from pathlib import Path

import pytest

from investorch.config import ConfigError, load_config

FIXTURES = Path(__file__).parents[1] / "fixtures" / "config"


def test_new_instance_loads_without_a_packaged_provider_or_files(tmp_path: Path) -> None:
    root = tmp_path / "new-instance"
    config = load_config(root=root)

    assert config.root == root
    assert config["models.main.name"] is None
    assert "models.main.name" in config.readiness()
    assert not root.exists()


@pytest.mark.parametrize(
    "fixture", ["legacy-secrets-only", "legacy-partial-models", "legacy-custom-models", "legacy-incomplete"]
)
def test_legacy_upgrade_preserves_effective_values_and_original_file(tmp_path: Path, fixture: str) -> None:
    local = tmp_path / "investorch.toml"
    original = (FIXTURES / f"{fixture}.toml").read_bytes()
    local.write_bytes(original)
    projected = load_config(root=tmp_path, migrate=False)
    assert local.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["investorch.toml"]

    migrated = load_config(root=tmp_path)
    assert migrated.public() == projected.public()
    assert migrated.secrets == projected.secrets
    assert (tmp_path / "investorch.toml.pre-schema.bak").read_bytes() == original
    saved = local.read_bytes()
    assert b"config_version = 1" in saved
    assert load_config(root=tmp_path).public() == migrated.public()
    assert local.read_bytes() == saved
    assert bool(migrated.readiness()) == (fixture == "legacy-incomplete")
    expected = "deepseek-flash" if fixture in ("legacy-secrets-only", "legacy-incomplete") else "custom-main"
    assert migrated.model("main").name == expected


def test_bad_legacy_config_never_gets_replaced_or_backed_up(tmp_path: Path) -> None:
    local = tmp_path / "investorch.toml"
    original = (FIXTURES / "legacy-invalid.toml").read_bytes()
    local.write_bytes(original)
    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(root=tmp_path)
    assert local.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["investorch.toml"]


def test_batch_update_is_atomic_and_restart_values_wait_until_reload(tmp_path: Path) -> None:
    config = load_config(root=tmp_path)
    config.ensure_local_file()
    original = config.root_config_path.read_bytes()
    with pytest.raises(ConfigError):
        config.update_many({"runtime.max_turns": 80, "web.default_port": 70000})
    assert config.root_config_path.read_bytes() == original
    assert config["runtime.max_turns"] == 100

    result = config.update_many({"runtime.max_turns": 80, "web.default_port": 8123})
    assert result["fields"]["runtime.max_turns"]["applied"] is True
    assert result["fields"]["web.default_port"]["requires_restart"] is True
    assert config["runtime.max_turns"] == 80
    assert config["web.default_port"] == 1334
    assert config.public(saved=True)["web"]["default_port"] == 8123
    reloaded = load_config(root=tmp_path)
    assert reloaded["runtime.max_turns"] == 80
    assert reloaded["web.default_port"] == 8123


def test_reset_removes_override_and_preserves_comments_secrets_and_snapshot(tmp_path: Path) -> None:
    local = tmp_path / "investorch.toml"
    local.write_text(
        'config_version = 1\n# User note\n[runtime]\nmax_turns = 25\n[secrets]\nKEY = "sensitive-test-token"\n'
    )
    config = load_config(root=tmp_path)
    snapshot = config.snapshot()
    config.reset(["runtime.max_turns"])
    assert snapshot["runtime.max_turns"] == 25
    assert config["runtime.max_turns"] == 100
    assert "runtime.max_turns" not in config.override_keys()
    assert b"# User note" in local.read_bytes()
    assert config.secret("KEY") == "sensitive-test-token"
    assert "sensitive-test-token" not in repr(config.public()) + repr(config.describe())


def test_external_edit_is_not_silently_overwritten(tmp_path: Path) -> None:
    config = load_config(root=tmp_path)
    config.ensure_local_file()
    config.root_config_path.write_text("config_version = 1\n[runtime]\nmax_turns = 12\n")
    before = config.root_config_path.read_bytes()
    with pytest.raises(ConfigError, match="changed outside"):
        config.update("runtime.max_turns", 15)
    assert config.root_config_path.read_bytes() == before
    assert config["runtime.max_turns"] == 100


def test_failed_atomic_replace_leaves_file_memory_and_no_incomplete_output(tmp_path: Path, monkeypatch) -> None:
    import os

    config = load_config(root=tmp_path)
    config.ensure_local_file()
    original = config.root_config_path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="filesystem failure"):
        config.update_many({"runtime.max_turns": 12, "secrets.NEW_KEY": "private-value"})
    assert config.root_config_path.read_bytes() == original
    assert config["runtime.max_turns"] == 100
    assert "NEW_KEY" not in config.secret_status()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["investorch.toml"]


def test_legacy_model_migration_preserves_field_comments(tmp_path: Path) -> None:
    local = tmp_path / "investorch.toml"
    local.write_text('[models.main]\nname = "custom" # my chosen model\n')
    load_config(root=tmp_path)
    assert "# my chosen model" in local.read_text()
