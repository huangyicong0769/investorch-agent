from pathlib import Path

import pytest

from investorch.application import current_run_options
from investorch.config import ConfigError
from investorch.context import AppState, ExecutionState
from tests.support.config import make_test_config


def test_future_run_overrides_do_not_change_configuration_or_frozen_run_options(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    before = config.public()
    file_before = config.root_config_path.read_bytes()
    state = AppState(config=config, execution=ExecutionState(), selected_session_id="session")
    started = current_run_options(state)
    state.main_reasoning_effort = "low"
    state.permission_mode = "manual"
    state.follow_up_behavior = "queue"
    future = current_run_options(state)
    assert (future.reasoning_effort, future.permission_mode, future.follow_up_behavior) == ("low", "manual", "queue")
    assert (started.reasoning_effort, started.permission_mode, started.follow_up_behavior) == ("max", "review", "steer")
    assert config.public() == before
    assert config.root_config_path.read_bytes() == file_before


def test_unset_overrides_follow_configuration_and_explicit_override_survives_changes(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    inherited = AppState(config, ExecutionState(), "inherited")
    overridden = AppState(config, ExecutionState(), "overridden", follow_up_behavior="steer")
    config.update("interaction.follow_up_behavior", "queue", persist=False)
    assert current_run_options(inherited).follow_up_behavior == "queue"
    assert current_run_options(overridden).follow_up_behavior == "steer"
    fresh = AppState(config, ExecutionState(), "fresh")
    assert fresh.follow_up_behavior == "queue"
    overridden.follow_up_behavior = None
    assert overridden.follow_up_behavior == "queue"


@pytest.mark.parametrize("field", ["main_reasoning_effort", "permission_mode", "follow_up_behavior"])
def test_invalid_temporary_defaults_leave_previous_values_unchanged(tmp_path: Path, field: str) -> None:
    config = make_test_config(tmp_path)
    state = AppState(config, ExecutionState(), "session")
    before = getattr(state, field)
    with pytest.raises(ConfigError, match="must be one of"):
        setattr(state, field, "invalid")
    assert getattr(state, field) == before


def test_existing_explicit_constructor_defaults_remain_supported(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    state = AppState(config, ExecutionState(), "session", "none", "manual")
    assert state.main_reasoning_effort == "none"
    assert state.permission_mode == "manual"
