"""Connections and role defaults resolve to complete execution settings."""

from pathlib import Path

import pytest

from investorch.config import ConfigError, load_config
from tests.support.config import make_test_config


def test_one_connection_can_supply_distinct_agent_models(tmp_path: Path) -> None:
    (tmp_path / "investorch.toml").write_text(
        '[models.shared]\ntype = "openai_compatible"\n'
        'base_url = "https://provider.example.test/v1"\napi_key_secret = "KEY"\n'
        '[agents.main]\nmodel = "shared"\nname = "large"\ncontext_window_tokens = 120000\n'
        '[agents.title]\nmodel = "shared"\nname = "small"\ncontext_window_tokens = 16000\n'
        '[agents.activity]\nmodel = "shared"\nname = "large"\n'
        '[agents.permission]\nmodel = "shared"\nname = "large"\n'
        '[agents.compact]\nmodel = "shared"\nname = "large"\n'
        '[secrets]\nKEY = "fixture-key"\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.readiness() == []
    assert config.agent_model("main").context_window_tokens == 120000
    assert config.agent_model("title").name == "small"
    assert config.agent_model("title").context_window_tokens == 16000
    assert config.agent_model("activity").context_window_tokens == 120000
    assert config.model_connection("shared").base_url == "https://provider.example.test/v1"


def test_old_role_model_tables_require_explicit_migration(tmp_path: Path) -> None:
    (tmp_path / "investorch.toml").write_text('[models.main]\nname = "legacy"\n')

    with pytest.raises(ConfigError, match="Breaking change"):
        load_config(tmp_path)


def test_model_overrides_require_explicit_cross_connection_settings(tmp_path: Path) -> None:
    config = make_test_config(
        tmp_path,
        {
            "models": {
                "other": {
                    "base_url": "https://other.example.test/v1",
                    "api_key_secret": "TEST_API_KEY",
                }
            }
        },
    )
    base = config.agent_model("main")

    assert config.resolve_model(base, reasoning_effort="low").context_window_tokens == 1000000
    with pytest.raises(ConfigError, match="requires context_window_tokens"):
        config.resolve_model(base, name="smaller")
    with pytest.raises(ConfigError, match="requires name, reasoning_effort and context_window_tokens"):
        config.resolve_model(base, model="other", name="smaller", context_window_tokens=32000)
    selected = config.resolve_model(
        base, model="other", name="smaller", reasoning_effort="low", context_window_tokens=32000
    )
    assert (selected.model, selected.name, selected.reasoning_effort, selected.context_window_tokens) == (
        "other",
        "smaller",
        "low",
        32000,
    )


def test_new_model_never_inherits_another_models_window(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    config.update_many({"agents.title.name": "different", "agents.title.context_window_tokens": None})
    config = load_config(config.root)
    assert "agents.title.context_window_tokens" in config.readiness()
    with pytest.raises(ConfigError, match=r"agents\.title\.context_window_tokens"):
        config.agent_model("title")


@pytest.mark.parametrize("window", [None, 0, -1, True, 1.5])
def test_invalid_effective_window_is_rejected(tmp_path: Path, window: object) -> None:
    from investorch.config import AgentModelConfig

    with pytest.raises(ConfigError, match="positive integer"):
        AgentModelConfig("test", "model", "high", window)


def test_oauth_updates_preserve_pending_settings_and_do_not_expose_tokens(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    config.update("logging.level", "DEBUG")
    config.save_oauth_credential(
        "personal",
        {
            "client_id": "issued",
            "subject": "verified",
            "access_token": "private-access",
            "refresh_token": "private-refresh",
            "id_token": "private-id",
            "expires_at": 9999999999,
            "scopes": ["chatgpt.tokens.use.direct"],
        },
    )
    config.update("interaction.follow_up_behavior", "queue")
    reloaded = load_config(config.root)
    assert reloaded["logging.level"] == "DEBUG"
    assert reloaded["interaction.follow_up_behavior"] == "queue"
    assert reloaded.oauth_credential("personal")["refresh_token"] == "private-refresh"
    assert "private-" not in repr(config.public())
    assert "personal" not in config.secrets
    assert "private-refresh" in config.secret_values()
    with pytest.raises(ConfigError, match="cannot be used as an API key"):
        config.secret("personal")
    assert config.root_config_path.stat().st_mode & 0o777 == 0o600


def test_connection_object_save_clears_inapplicable_fields_and_requires_restart(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    config.update(
        "models.test", {"type": "chatgpt", "credentials": "personal", "base_url": None, "api_key_secret": None}
    )

    assert config.model_connection("test").type == "openai_compatible"
    assert load_config(config.root).model_connection("test").type == "chatgpt"
    config.activate_saved_for_startup()
    assert config.model_connection("test").credentials == "personal"
