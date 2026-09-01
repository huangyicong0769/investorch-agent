from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.support.runtime import ControllableSteerSink, make_runtime_harness, run_options


@pytest.mark.asyncio
async def test_steer_submission_returns_only_after_it_is_durable(tmp_path: Path) -> None:
    base = make_runtime_harness(tmp_path)
    sink = ControllableSteerSink(base.journal)
    await base.runtime.aclose()
    harness = make_runtime_harness(tmp_path / "runtime", record_user_steer=sink.record)
    harness.runtime.start_run("session-a", "current", run_options("steer"))
    await harness.agent_loop.wait_until_started("session-a")

    submission_task = asyncio.create_task(harness.runtime.submit_follow_up("session-a", "steer", run_options()))
    await sink.wait_until_write_started()
    assert submission_task.done() is False

    sink.release()
    submission = await submission_task
    event = await harness.wait_for_follow_up("steer_submitted")
    assert submission.behavior == "steer"
    assert event.journal_seq is not None

    harness.runtime.cancel_run("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == "cancelled"
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_steer_journal_failure_does_not_pollute_the_active_run(tmp_path: Path) -> None:
    base = make_runtime_harness(tmp_path)
    sink = ControllableSteerSink(base.journal, error=RuntimeError("journal unavailable"))
    await base.runtime.aclose()
    harness = make_runtime_harness(tmp_path / "runtime", record_user_steer=sink.record)
    harness.runtime.start_run("session-a", "current", run_options("steer"))
    await harness.agent_loop.wait_until_started("session-a")

    submission_task = asyncio.create_task(harness.runtime.submit_follow_up("session-a", "steer", run_options()))
    await sink.wait_until_write_started()
    sink.release()
    with pytest.raises(RuntimeError, match="journal unavailable"):
        await submission_task

    snapshot = harness.runtime.session_snapshot("session-a")
    assert snapshot.run_id is not None
    assert snapshot.pending_steer_count == 0

    harness.agent_loop.complete("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == "completed"
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_steer_event_preserves_session_and_run_attribution(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    active = harness.runtime.start_run("session-a", "current", run_options("steer"))
    await harness.agent_loop.wait_until_started("session-a")

    submission = await harness.runtime.submit_follow_up("session-a", "steer", run_options())
    event = await harness.wait_for_follow_up("steer_submitted")

    assert event.session_id == "session-a"
    assert event.run_id == active.run_id
    assert event.source_run_id == active.run_id
    assert event.follow_up_id == submission.follow_up_id
    assert event.journal_seq is not None

    harness.runtime.cancel_run("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == "cancelled"
    await harness.runtime.aclose()
