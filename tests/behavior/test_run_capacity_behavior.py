from pathlib import Path

import pytest

from investorch.config import ConfigError, load_config
from investorch.journal import read_session_journal
from investorch.runtime import RunCapacityExceededError
from tests.support.runtime import make_runtime_harness, run_options
from tests.support.web import open_test_web


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


async def test_capacity_pauses_queue_without_losing_fifo_options_or_failing_the_finished_run(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path, journal_run_ended=True)
    try:
        first = harness.runtime.start_run("a", "first", run_options("queue"))
        harness.runtime.start_run("b", "occupy", run_options())
        for name in ("a", "b"):
            await harness.agent_loop.wait_until_started(name)
        queued = await harness.runtime.submit_follow_up("a", "follow-up", run_options("queue"))
        harness.config.update("runtime.max_concurrent_runs", 1, persist=False)
        harness.agent_loop.complete("a")
        await first.task

        snapshot = harness.runtime.session_snapshot("a")
        assert snapshot.queue_paused and snapshot.queued_count == 1
        assert harness.runtime.list_queued_inputs("a")[0].queue_id == queued.follow_up_id
        assert harness.run_ended[0].status == "completed"
        with pytest.raises(RunCapacityExceededError):
            await harness.runtime.resume_queue("a")
        assert harness.runtime.session_snapshot("a").queue_paused
        assert [
            r["text"]
            for r in read_session_journal(harness.config.session_journal_dir, "a")
            if r["type"] == "user_message"
        ] == ["first"]
        harness.agent_loop.complete("b")
        await harness.wait_for_run_ended("b")
        assert harness.runtime.session_snapshot("a").queue_paused
        await harness.runtime.resume_queue("a")
        await harness.agent_loop.wait_until_started("a", occurrence=2)
        assert harness.runtime.get_active_run("a").options == run_options("queue")
        assert [
            r["text"]
            for r in read_session_journal(harness.config.session_journal_dir, "a")
            if r["type"] == "user_message"
        ] == ["first", "follow-up"]
    finally:
        await harness.runtime.aclose()


async def test_capacity_retains_durable_steer_fallback_and_recovers_when_another_run_ends(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path, journal_run_ended=True)
    try:
        first = harness.runtime.start_run("a", "first", run_options())
        other = harness.runtime.start_run("b", "occupy", run_options())
        for name in ("a", "b"):
            await harness.agent_loop.wait_until_started(name)
        await harness.runtime.submit_follow_up("a", "steer", run_options())
        harness.config.update("runtime.max_concurrent_runs", 1, persist=False)
        harness.agent_loop.complete("a")
        await first.task
        assert harness.run_ended[0].status == "completed"
        assert harness.runtime.session_snapshot("a").pending_steer_count == 1
        records = read_session_journal(harness.config.session_journal_dir, "a")
        source = next(r["seq"] for r in records if r["type"] == "user_steer")

        harness.agent_loop.complete("b")
        await other.task
        await harness.agent_loop.wait_until_started("a", occurrence=2)
        records = read_session_journal(harness.config.session_journal_dir, "a")
        assert [r["text"] for r in records if r["type"] == "user_message"] == ["first"]
        assert [r["seq"] for r in records if r["type"] == "user_steer"] == [source]
        activation = next(r for r in records if r["type"] == "user_steers_activated")
        assert activation["user_steer_seqs"] == [source]
        assert harness.agent_loop.input_for("a").user_input == "steer"
    finally:
        await harness.runtime.aclose()


async def test_web_rejects_new_runs_with_a_corrective_capacity_error(tmp_path: Path) -> None:
    async with open_test_web(tmp_path, {"runtime": {"max_concurrent_runs": 1}}, raise_app_exceptions=False) as web:
        first = await web.host.sessions.create()
        other = await web.host.sessions.create()
        web.host.runtime.start_run(first, "occupy", run_options())
        await web.runtime.agent_loop.wait_until_started(first)
        response = await web.client.post(f"/api/sessions/{other}/messages", json={"text": "next"})
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "capacity_exceeded"
        assert response.json()["error"]["details"] == {"active": 1, "limit": 1}
        assert web.host.runtime.session_snapshot(other).run_id is None
