import tomllib
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from investorch.config_schema import AppConfigSchema, config_field_metadata


def test_schema_defaults_preserve_legacy_values_except_unconfigured_provider() -> None:
    fixture = Path(__file__).parents[1] / "fixtures/config/legacy-0.1-defaults.toml"
    expected = tomllib.loads(fixture.read_text())
    expected["paths"]["root"] = str(Path(expected["paths"]["root"]).expanduser().resolve())
    for role in expected["models"].values():
        role.update(name=None, base_url=None, api_key_secret=None)
        role.setdefault("context_window_tokens", None)
    expected["secrets"] = {}
    assert AppConfigSchema.model_validate({}).model_dump() == expected


def test_schema_describes_lifecycle_and_secret_sensitivity() -> None:
    assert config_field_metadata("paths.root")["apply_mode"] == "immutable"
    assert config_field_metadata("models.custom.reasoning_effort")["apply_mode"] == "restart_required"
    assert config_field_metadata("runtime.max_turns")["apply_mode"] == "hot"
    assert config_field_metadata("secrets.PROVIDER_KEY")["sensitive"] is True
    assert config_field_metadata("secrets.PROVIDER_KEY")["type"] == "string"


def test_schema_exposes_standard_numeric_bounds_for_generated_forms() -> None:
    ratio = config_field_metadata("compaction.trigger_ratio")
    assert ratio["exclusiveMinimum"] == 0
    assert ratio["exclusiveMaximum"] == 1
    assert config_field_metadata("mcp.default_timeout_seconds")["exclusiveMinimum"] == 0
    assert config_field_metadata("backtest.slippage")["minimum"] == 0


@pytest.mark.parametrize(
    ("section", "overrides", "message"),
    [
        ("images", {"max_images_per_input": 0}, "max_images_per_input"),
        ("images", {"max_image_bytes": -1}, "max_image_bytes"),
        ("images", {"max_total_image_bytes": True}, "max_total_image_bytes"),
        ("images", {"default_detail": "unsupported"}, "default_detail"),
        ("activity", {"max_label_chars": 0}, "max_label_chars"),
        ("compaction", {"trigger_ratio": 0}, "trigger_ratio"),
        ("compaction", {"trigger_ratio": 1}, "trigger_ratio"),
        ("compaction", {"trigger_ratio": float("nan")}, "trigger_ratio"),
        ("compaction", {"auto_enabled": 1}, "auto_enabled"),
        ("permission", {"mode": "unrestricted"}, "mode"),
        ("permission", {"max_user_instruction_chars": 100}, "max_compacted_instruction_chars"),
        ("permission", {"max_user_message_chars": 16000}, "renamed to permission.max_user_instruction_chars"),
        ("interaction", {"follow_up_behavior": "interrupt"}, "follow_up_behavior"),
        ("tui", {"sidebar_min_width": 31}, "sidebar_min_width"),
        ("tui", {"composer_height": 3}, "composer_height"),
        ("tui", {"todo_collapsible_max_height": 8}, "todo_collapsible_max_height"),
        ("tui", {"todo_contents_max_height": 7}, "todo_contents_max_height"),
        ("tui", {"message_author_margin_top": 2}, "message_author_margin_top"),
        ("tui", {"run_timer_interval_seconds": 0}, "run_timer_interval_seconds"),
        ("web", {"default_port": 65536}, "default_port"),
        ("web", {"default_port": 0}, "default_port"),
        ("web", {"websocket_reconnect_max_delay_ms": 499}, "websocket_reconnect_max_delay_ms"),
        ("web", {"unused_session_discard_delay_ms": -1}, "unused_session_discard_delay_ms"),
        ("logging", {"level": "debug"}, "level"),
        ("logging", {"backup_count": -1}, "backup_count"),
        ("runtime", {"default_timezone": "Missing/Timezone"}, "IANA timezone"),
        ("runtime", {"max_turns": True}, "max_turns"),
        ("runtime", {"max_turns": "100"}, "max_turns"),
        ("runtime", {"max_turns": 100.0}, "max_turns"),
        ("runtime", {"max_turns": 0}, "max_turns"),
        ("execution", {"max_timeout_seconds": 29}, "max_timeout_seconds"),
        ("explore", {"max_read_chars": 0}, "max_read_chars"),
        ("calculate", {"max_integer_bits": 0}, "max_integer_bits"),
        ("backtest", {"default_initial_cash": 0}, "default_initial_cash"),
        ("backtest", {"capital_gain_tax_rate": -1}, "capital_gain_tax_rate"),
        ("backtest", {"slippage": float("inf")}, "slippage"),
        ("backtest", {"matching_type": "next_bar"}, "matching_type"),
        ("backtest", {"slippage_model": "Other"}, "slippage_model"),
        ("mcp", {"default_timeout_seconds": True}, "default_timeout_seconds"),
        ("mcp", {"default_timeout_seconds": "5"}, "default_timeout_seconds"),
        ("mcp", {"default_timeout_seconds": Decimal("5")}, "default_timeout_seconds"),
        ("mcp", {"default_timeout_seconds": 0}, "default_timeout_seconds"),
        ("cnequity", {"mcp_cache_tools_list": "true"}, "mcp_cache_tools_list"),
        ("secrets", {"KEY": 123}, "KEY"),
        ("secrets", {"invalid.name": "key"}, "invalid.name"),
        ("runtime", {"unknown": 1}, "Extra inputs are not permitted"),
        ("unknown", {}, "Extra inputs are not permitted"),
    ],
)
def test_schema_rejects_invalid_configuration(section: str, overrides: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        AppConfigSchema.model_validate({section: overrides})


@pytest.mark.parametrize("value", [1, 0.5])
def test_schema_accepts_positive_integer_and_float_timeouts(value: int | float) -> None:
    assert (
        AppConfigSchema.model_validate({"mcp": {"default_timeout_seconds": value}}).mcp.default_timeout_seconds == value
    )


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("paths", "root", "relative", "must be absolute"),
        ("paths", "workspace", ".", "strict subdirectory"),
        ("paths", "state", ".", "strict subdirectory"),
        ("paths", "workspace", "state", "must not overlap"),
        ("paths", "state", "workspace/nested", "must not overlap"),
        ("paths", "workspace", "state/nested", "must not overlap"),
        ("paths", "workspace", "../outside", "escapes paths.root"),
        ("paths", "state", "/tmp", "must be relative"),
        ("execution", "background_job_dir", "../outside", "escapes paths.root"),
        ("backtest", "artifact_dir", "../outside", "escapes paths.root"),
        ("backtest", "rqalpha_bundle_dir", "../outside", "escapes paths.root"),
        ("cnequity", "config_path", "../outside", "escapes paths.root"),
    ],
)
def test_schema_enforces_directory_boundaries(
    tmp_path: Path, section: str, field: str, value: str, message: str
) -> None:
    data = {"paths": {"root": str(tmp_path)}}
    data.setdefault(section, {})[field] = value
    with pytest.raises(ValidationError, match=message):
        AppConfigSchema.model_validate(data)


def test_schema_rejects_symlink_escape_and_root_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "workspace").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValidationError, match=r"escapes paths\.root"):
        AppConfigSchema.model_validate({"paths": {"root": str(root)}})
    file = tmp_path / "file"
    file.write_text("not a directory")
    with pytest.raises(ValidationError, match="not a directory"):
        AppConfigSchema.model_validate({"paths": {"root": str(file)}})


def test_schema_accepts_independent_providers_and_extra_named_model_roles() -> None:
    fixture = Path(__file__).parents[1] / "fixtures/config/legacy-0.1-defaults.toml"
    data = tomllib.loads(fixture.read_text())
    data["models"]["analyst"] = {
        "name": "another-model",
        "base_url": "https://provider.example/v1",
        "api_key_secret": "OTHER_KEY",
        "reasoning_effort": "low",
    }
    data["secrets"] = {"OTHER_KEY": "test-key"}
    result = AppConfigSchema.model_validate(data)
    assert result.models["analyst"].name == "another-model"
    assert result.models["main"].name == "deepseek-flash"
    assert "test-key" not in str(result.model_json_schema())


@pytest.mark.parametrize("field", ["name", "base_url", "api_key_secret"])
def test_supplied_model_settings_cannot_be_blank(field: str) -> None:
    data = AppConfigSchema().model_dump()
    data["models"]["main"][field] = " "
    with pytest.raises(ValidationError, match=field):
        AppConfigSchema.model_validate(data)


@pytest.mark.parametrize("role", ["", "nested.role"])
def test_extra_model_roles_require_valid_names(role: str) -> None:
    data = AppConfigSchema().model_dump()
    data["models"][role] = data["models"]["title"]
    with pytest.raises(ValidationError):
        AppConfigSchema.model_validate(data)


def test_required_model_roles_and_main_context_cannot_be_removed() -> None:
    data = AppConfigSchema().model_dump()
    del data["models"]["title"]
    with pytest.raises(ValidationError, match="Missing required model: title"):
        AppConfigSchema.model_validate(data)
    data = AppConfigSchema().model_dump()
    data["models"]["main"]["context_window_tokens"] = None
    with pytest.raises(ValidationError, match=r"models\.main\.context_window_tokens"):
        AppConfigSchema.model_validate(data)


@pytest.mark.parametrize(
    "key", ["runtime.unknown", "models.main.unknown", "secrets.KEY.nested", "models.main", "paths", "secrets."]
)
def test_schema_metadata_rejects_unknown_keys_and_tables(key: str) -> None:
    with pytest.raises(ValueError):
        config_field_metadata(key)
