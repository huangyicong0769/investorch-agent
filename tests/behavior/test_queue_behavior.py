from __future__ import annotations

from pathlib import Path

import pytest

from qmt_agent.journal import read_session_journal
from tests.support.runtime import FailingTextUserMessageSink, make_runtime_harness, run_options


@pytest.mark.asyncio
async def test_queue_submission_is_not_journaled_until_promotion(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")

    submission = await harness.runtime.submit_follow_up("session-a", "queued", run_options())

    snapshot = harness.runtime.session_snapshot("session-a")
    records = read_session_journal(harness.config.session_journal_dir, "session-a")
    assert submission.behavior == "queue"
    assert snapshot.queued_count == 1
    assert [record["text"] for record in records] == ["current"]

    harness.runtime.clear_queue("session-a")
    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_successful_completion_promotes_durable_queue_head(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "queued", run_options())

    harness.agent_loop.complete("session-a")
    await harness.agent_loop.wait_until_started("session-a", occurrence=2)

    records = read_session_journal(harness.config.session_journal_dir, "session-a")
    promoted = await harness.wait_for_follow_up("queue_promoted")
    assert [record["text"] for record in records] == ["current", "queued"]
    assert promoted.text == "queued"
    assert promoted.journal_seq == 2
    assert harness.runtime.session_snapshot("session-a").queued_count == 0

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a", occurrence=2)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_queue_promotes_in_fifo_order(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    for text in ("Q1", "Q2", "Q3"):
        await harness.runtime.submit_follow_up("session-a", text, run_options("queue"))

    for occurrence in range(1, 5):
        harness.agent_loop.complete("session-a")
        await harness.wait_for_run_ended("session-a", occurrence=occurrence)
        if occurrence < 4:
            await harness.agent_loop.wait_until_started("session-a", occurrence=occurrence + 1)

    records = read_session_journal(harness.config.session_journal_dir, "session-a")
    assert [record["text"] for record in records] == ["current", "Q1", "Q2", "Q3"]
    assert [event.text for event in harness.follow_ups if event.kind == "queue_promoted"] == ["Q1", "Q2", "Q3"]
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_queue_item_captures_follow_up_default_at_submission(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "Q1", run_options("steer"))

    harness.agent_loop.complete("session-a")
    await harness.agent_loop.wait_until_started("session-a", occurrence=2)

    assert harness.runtime.session_snapshot("session-a").active_follow_up_behavior == "steer"
    submission = await harness.runtime.submit_follow_up("session-a", "during Q1", run_options("queue"))
    assert submission.behavior == "steer"

    harness.runtime.cancel_run("session-a")
    assert (await harness.wait_for_run_ended("session-a", occurrence=2)).status == "cancelled"
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_failed_queue_promotion_keeps_head_and_pauses_queue(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    sink = FailingTextUserMessageSink(harness.journal, "Q1")
    journal_dir = harness.config.session_journal_dir
    await harness.runtime.aclose()
    harness = make_runtime_harness(tmp_path / "runtime", record_user_message=sink.record)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "Q1", run_options())

    harness.agent_loop.complete("session-a")

    snapshot = await harness.wait_for_snapshot(
        "session-a",
        lambda state: state.queue_paused and state.run_id is None,
    )
    assert snapshot.queued_count == 1
    assert [item.text for item in harness.runtime.list_queued_inputs("session-a")] == ["Q1"]
    assert [record["text"] for record in read_session_journal(journal_dir, "session-a")] == ["current"]
    assert [event for event in harness.follow_ups if event.kind == "queue_promoted"] == []
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_stop_preserves_and_pauses_queued_intent(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "Q1", run_options())
    await harness.runtime.submit_follow_up("session-a", "Q2", run_options())

    harness.runtime.cancel_run("session-a")
    ended = await harness.wait_for_run_ended("session-a")

    snapshot = harness.runtime.session_snapshot("session-a")
    assert ended.status == "cancelled"
    assert snapshot.queue_paused is True
    assert snapshot.queued_count == 2
    assert [item.text for item in harness.runtime.list_queued_inputs("session-a")] == ["Q1", "Q2"]
    assert [record["text"] for record in read_session_journal(harness.config.session_journal_dir, "session-a")] == ["current"]
    assert [event for event in harness.follow_ups if event.kind == "queue_promoted"] == []
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_resume_promotes_the_paused_queue_head(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "Q1", run_options())
    await harness.runtime.submit_follow_up("session-a", "Q2", run_options())
    harness.runtime.cancel_run("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == "cancelled"

    await harness.runtime.resume_queue("session-a")
    await harness.agent_loop.wait_until_started("session-a", occurrence=2)

    assert [record["text"] for record in read_session_journal(harness.config.session_journal_dir, "session-a")] == ["current", "Q1"]
    assert [item.text for item in harness.runtime.list_queued_inputs("session-a")] == ["Q2"]
    harness.runtime.clear_queue("session-a")
    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a", occurrence=2)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_clear_queue_keeps_committed_history_unchanged(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "committed", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "future", run_options())
    history_before = read_session_journal(harness.config.session_journal_dir, "session-a")

    removed = harness.runtime.clear_queue("session-a")

    assert removed == 1
    assert harness.runtime.session_snapshot("session-a").queued_count == 0
    assert read_session_journal(harness.config.session_journal_dir, "session-a") == history_before
    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_removing_one_queue_item_preserves_remaining_fifo_order(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "current", run_options("queue"))
    await harness.agent_loop.wait_until_started("session-a")
    submissions = [
        await harness.runtime.submit_follow_up("session-a", text, run_options())
        for text in ("Q1", "Q2", "Q3")
    ]

    removed = harness.runtime.remove_queued_input("session-a", submissions[1].follow_up_id)

    assert removed.text == "Q2"
    assert [item.text for item in harness.runtime.list_queued_inputs("session-a")] == ["Q1", "Q3"]
    harness.runtime.clear_queue("session-a")
    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()
