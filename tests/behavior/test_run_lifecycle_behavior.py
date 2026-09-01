from __future__ import annotations

import asyncio
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qmt_agent.journal import SessionJournal, read_session_journal
from qmt_agent.runtime import SessionBusyError
from tests.support.config import make_test_config
from tests.support.runtime import ControllableUserMessageSink, make_runtime_harness, run_options


@pytest.mark.asyncio
async def test_other_session_can_start_while_first_session_is_running(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "first", run_options())
    await harness.agent_loop.wait_until_started("session-a")

    harness.runtime.start_run("session-b", "second", run_options())
    await harness.agent_loop.wait_until_started("session-b")

    assert harness.runtime.session_snapshot("session-a").run_id is not None
    assert harness.runtime.session_snapshot("session-b").run_id is not None

    harness.agent_loop.complete("session-b")
    ended = await harness.wait_for_run_ended("session-b")
    assert ended.status == "completed"
    assert harness.runtime.session_snapshot("session-b").run_id is None
    assert harness.runtime.session_snapshot("session-a").run_id is not None

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_same_session_rejects_a_second_top_level_run(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "first", run_options())
    await harness.agent_loop.wait_until_started("session-a")

    with pytest.raises(SessionBusyError):
        harness.runtime.start_run("session-a", "second", run_options())

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_completed_run_returns_session_to_idle_and_allows_the_next_run(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "first", run_options())
    await harness.agent_loop.wait_until_started("session-a")
    harness.agent_loop.complete("session-a")

    first_ended = await harness.wait_for_run_ended("session-a")
    assert first_ended.status == "completed"
    assert harness.runtime.session_snapshot("session-a").run_id is None

    second = harness.runtime.start_run("session-a", "second", run_options())
    await harness.agent_loop.wait_until_started("session-a", occurrence=2)
    assert second.run_id != first_ended.run_id

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a", occurrence=2)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_failed_run_returns_session_to_idle_and_allows_recovery(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.agent_loop.fail_input("fail")
    failed = harness.runtime.start_run("session-a", "fail", run_options())
    await harness.agent_loop.wait_until_started("session-a")
    harness.agent_loop.complete("session-a")

    with pytest.raises(RuntimeError, match="controlled Agent failure"):
        await failed.task
    ended = await harness.wait_for_run_ended("session-a")
    assert ended.status == "failed"
    assert harness.runtime.session_snapshot("session-a").run_id is None

    harness.runtime.start_run("session-a", "recovery", run_options())
    await harness.agent_loop.wait_until_started("session-a", occurrence=2)
    harness.agent_loop.complete("session-a")
    assert (await harness.wait_for_run_ended("session-a", occurrence=2)).status == "completed"
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_agent_execution_waits_for_initial_user_message_durability(tmp_path: Path) -> None:
    journal_config = make_test_config(tmp_path / "journal")
    journal = SessionJournal(journal_config.session_journal_dir, ZoneInfo("UTC"))
    sink = ControllableUserMessageSink(journal)
    harness = make_runtime_harness(tmp_path / "runtime", record_user_message=sink.record)

    harness.runtime.start_run("session-a", "durable first", run_options())
    await sink.wait_until_write_started()

    assert harness.outputs == []
    assert harness.runtime.session_snapshot("session-a").run_id is not None

    sink.release()
    output = await harness.wait_for_output("session-a")
    records = read_session_journal(journal_config.session_journal_dir, "session-a")
    assert output.session_id == "session-a"
    assert [record["text"] for record in records] == ["durable first"]

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_initial_journal_failure_prevents_agent_execution(tmp_path: Path) -> None:
    journal_config = make_test_config(tmp_path / "journal")
    journal = SessionJournal(journal_config.session_journal_dir, ZoneInfo("UTC"))
    sink = ControllableUserMessageSink(journal, error=RuntimeError("journal unavailable"))
    harness = make_runtime_harness(tmp_path / "runtime", record_user_message=sink.record)
    active = harness.runtime.start_run("session-a", "never execute", run_options())
    await sink.wait_until_write_started()

    sink.release()
    with pytest.raises(RuntimeError, match="journal unavailable"):
        await active.task

    ended = await harness.wait_for_run_ended("session-a")
    assert ended.status == "failed"
    assert harness.runtime.session_snapshot("session-a").run_id is None
    assert harness.outputs == []
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_stop_is_immediately_visible_then_run_ends_cancelled(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    active = harness.runtime.start_run("session-a", "long running", run_options())
    await harness.agent_loop.wait_until_started("session-a")

    harness.runtime.cancel_run("session-a")

    assert harness.runtime.session_snapshot("session-a").run_phase == "stopping"
    with pytest.raises(asyncio.CancelledError):
        await active.task
    ended = await harness.wait_for_run_ended("session-a")
    assert ended.status == "cancelled"
    assert harness.runtime.session_snapshot("session-a").run_id is None
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_closed_runtime_cancels_active_work_and_rejects_new_runs(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "long running", run_options())
    await harness.agent_loop.wait_until_started("session-a")

    await harness.runtime.aclose()

    assert harness.runtime.session_snapshot("session-a").run_id is None
    with pytest.raises(RuntimeError, match="closed"):
        harness.runtime.start_run("session-b", "new", run_options())
