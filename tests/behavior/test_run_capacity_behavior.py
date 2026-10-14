from pathlib import Path

import pytest

from investorch.config import ConfigError, load_config
from investorch.runtime import RunCapacityExceededError
from tests.support.runtime import make_runtime_harness, run_options


async def test_all_new_runs_share_the_default_capacity_and_release_slots(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    try:
        runs = [harness.runtime.start_run(name, name, run_options()) for name in ("a", "b", "c")]
        for name in ("a", "b", "c"):
            await harness.agent_loop.wait_until_started(name)
        with pytest.raises(RunCapacityExceededError) as rejected:
            harness.runtime.start_run("d", "fourth", run_options())
        assert (rejected.value.active, rejected.value.limit) == (3, 3)
        assert harness.runtime.session_snapshot("d").run_id is None
        harness.agent_loop.complete("a")
        await runs[0].task
        fourth = harness.runtime.start_run("d", "fourth", run_options())
        await harness.agent_loop.wait_until_started("d")
        assert fourth.run_id != runs[0].run_id
    finally:
        await harness.runtime.aclose()


async def test_hot_capacity_updates_only_affect_new_runs(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    try:
        for name in ("a", "b", "c"):
            harness.runtime.start_run(name, name, run_options())
            await harness.agent_loop.wait_until_started(name)
        result = harness.config.update("runtime.max_concurrent_runs", 1, persist=True)
        assert result["applied"] is True
        assert load_config(root=harness.config.root)["runtime.max_concurrent_runs"] == 1
        assert len(harness.runtime.list_active_runs()) == 3
        with pytest.raises(RunCapacityExceededError):
            harness.runtime.start_application_run("d", "application", run_options())
        harness.config.update("runtime.max_concurrent_runs", 4, persist=False)
        harness.runtime.start_contextual_run("d", "user", "context", run_options())
        await harness.agent_loop.wait_until_started("d")
        assert len(harness.runtime.list_active_runs()) == 4
    finally:
        await harness.runtime.aclose()


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "4", "invalid"])
def test_invalid_capacity_updates_leave_the_effective_and_saved_config_unchanged(tmp_path: Path, value) -> None:
    harness = make_runtime_harness(tmp_path)
    before = harness.config.root_config_path.read_bytes()
    with pytest.raises(ConfigError):
        harness.config.update("runtime.max_concurrent_runs", value, persist=True)
    assert harness.config["runtime.max_concurrent_runs"] == 3
    assert harness.config.root_config_path.read_bytes() == before
