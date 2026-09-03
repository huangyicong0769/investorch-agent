from __future__ import annotations

import asyncio
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import Agent
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.application import PortfolioOperations, ReviewContext
from investorch.context import AgentContext, ExecutionState
from investorch.journal import SessionJournal, read_session_journal
from investorch.runtime import AgentRuntime, ApprovalRequest, RuntimeOutput, RuntimeRunEnded
from investorch.storage import create_session, set_session_title
from investorch.tools import create_portfolio
from tests.support.config import make_test_config
from tests.support.runtime import make_runtime_harness, run_options


@pytest.mark.asyncio
async def test_activated_steer_advances_the_next_approval_instruction_boundary(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo(config["runtime.default_timezone"]))
    portfolios = PortfolioOperations(config=config)
    create_session(config.sessions_db, "session-a")
    set_session_title(config.sessions_db, "session-a", "Test")
    first_model_started = asyncio.Event()
    release_first_model = asyncio.Event()
    two_runs_ended = asyncio.Event()
    approval_requests: list[ApprovalRequest] = []
    run_ended: list[RuntimeRunEnded] = []

    async def first_step(_call: object) -> tuple[object, ...]:
        first_model_started.set()
        await release_first_model.wait()
        return (assistant_message("first turn"),)

    model = ScriptedModel(
        (
            ModelStep.respond(first_step),
            (function_call("create_portfolio", {"name": "Core", "base_currency": "CNY"}, call_id="call-a"),),
            (assistant_message("done"),),
        )
    )
    agent = Agent[AgentContext](name="Main", instructions="test", model=model, tools=[create_portfolio])
    unused_agent = Agent(name="Unused", instructions="test", model=ScriptedModel())
    loop = AgentLoop(agent, unused_agent, unused_agent, config, portfolios)

    async def approval_handler(request: ApprovalRequest) -> ApprovalOutcome:
        approval_requests.append(request)
        return ApprovalOutcome(approved=False, usage=TokenUsage())

    async def output_handler(_output: RuntimeOutput) -> None:
        pass

    async def run_ended_handler(event: RuntimeRunEnded) -> None:
        run_ended.append(event)
        if len(run_ended) == 2:
            two_runs_ended.set()

    runtime = AgentRuntime(
        loop,
        ExecutionState(workspace_root=config.workspace_dir),
        config.sessions_db,
        output_handler,
        approval_handler,
        journal.record_user_message,
        journal.record_user_steer,
        journal.record_user_steers_activated,
        journal.record_user_steers_discarded,
        run_ended_handler=run_ended_handler,
    )
    runtime.start_run("session-a", "initial instruction", run_options("steer"))
    await first_model_started.wait()
    await runtime.submit_follow_up("session-a", "later instruction", run_options())
    release_first_model.set()

    await asyncio.wait_for(two_runs_ended.wait(), timeout=2)
    await runtime.aclose()

    records = read_session_journal(config.session_journal_dir, "session-a")
    steer = next(record for record in records if record["type"] == "user_steer")
    activation = next(record for record in records if record["type"] == "user_steers_activated")
    assert [request.instruction_head_seq for request in approval_requests] == [activation["seq"]]
    assert activation["user_steer_seqs"] == [steer["seq"]]
    assert [event.status for event in run_ended] == ["completed", "completed"]
    model.assert_complete()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["cancelled", "failed"])
async def test_unsuccessful_run_discards_unactivated_steer_from_later_review_context(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    harness = make_runtime_harness(tmp_path, journal_run_ended=True)
    active = harness.runtime.start_run("session-a", "initial instruction", run_options("steer"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "abandoned instruction", run_options())

    if terminal_status == "cancelled":
        harness.runtime.cancel_run("session-a")
    else:
        harness.agent_loop.fail_input("initial instruction")
        harness.agent_loop.complete("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == terminal_status
    if terminal_status == "failed":
        with pytest.raises(RuntimeError, match="controlled Agent failure"):
            await active.task

    harness.runtime.start_run("session-a", "later instruction", run_options("steer"))
    await harness.agent_loop.wait_until_started("session-a", occurrence=2)
    records = read_session_journal(harness.config.session_journal_dir, "session-a")
    later_head_seq = next(
        record["seq"]
        for record in reversed(records)
        if record["type"] == "user_message" and record["text"] == "later instruction"
    )

    prepared = await ReviewContext(config=harness.config).prepare("session-a", later_head_seq)

    discarded = next(record for record in records if record["type"] == "user_steers_discarded")
    abandoned = next(record for record in records if record["type"] == "user_steer")
    assert discarded["user_steer_seqs"] == [abandoned["seq"]]
    assert prepared.instruction_count == 2
    assert "initial instruction" in prepared.text
    assert "later instruction" in prepared.text
    assert "abandoned instruction" not in prepared.text

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a", occurrence=2)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_cancel_after_activation_commit_does_not_add_a_conflicting_discard(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo(config["runtime.default_timezone"]))
    portfolios = PortfolioOperations(config=config)
    create_session(config.sessions_db, "session-a")
    set_session_title(config.sessions_db, "session-a", "Test")
    approval_started = asyncio.Event()
    release_approval = asyncio.Event()
    activation_committed = asyncio.Event()
    release_activation = asyncio.Event()
    run_ended: list[RuntimeRunEnded] = []

    async def delayed_activation(
        session_id: str,
        run_id: str,
        steer_seqs: tuple[int, ...],
    ) -> int:
        activation_seq = await journal.record_user_steers_activated(session_id, run_id, steer_seqs)
        activation_committed.set()
        await release_activation.wait()
        return activation_seq

    async def approval_handler(_request: ApprovalRequest) -> ApprovalOutcome:
        approval_started.set()
        await release_approval.wait()
        return ApprovalOutcome(approved=False, usage=TokenUsage())

    async def output_handler(_output: RuntimeOutput) -> None:
        pass

    async def run_ended_handler(event: RuntimeRunEnded) -> None:
        run_ended.append(event)

    model = ScriptedModel(
        ((function_call("create_portfolio", {"name": "Core", "base_currency": "CNY"}, call_id="call-a"),),)
    )
    unused_agent = Agent(name="Unused", instructions="test", model=ScriptedModel())
    loop = AgentLoop(
        Agent[AgentContext](name="Main", instructions="test", model=model, tools=[create_portfolio]),
        unused_agent,
        unused_agent,
        config,
        portfolios,
    )
    runtime = AgentRuntime(
        loop,
        ExecutionState(workspace_root=config.workspace_dir),
        config.sessions_db,
        output_handler,
        approval_handler,
        journal.record_user_message,
        journal.record_user_steer,
        delayed_activation,
        journal.record_user_steers_discarded,
        run_ended_handler=run_ended_handler,
    )
    active = runtime.start_run("session-a", "initial instruction", run_options("steer"))
    await approval_started.wait()
    await runtime.submit_follow_up("session-a", "active steer", run_options())
    release_approval.set()
    await activation_committed.wait()

    runtime.cancel_run("session-a")
    await asyncio.sleep(0)
    release_activation.set()
    with pytest.raises(asyncio.CancelledError):
        await active.task

    later_head_seq = await journal.record_user_message("session-a", "later instruction")
    prepared = await ReviewContext(config=config).prepare("session-a", later_head_seq)
    records = read_session_journal(config.session_journal_dir, "session-a")
    assert [record["type"] for record in records].count("user_steers_activated") == 1
    assert all(record["type"] != "user_steers_discarded" for record in records)
    assert "active steer" in prepared.text
    assert run_ended[0].status == "cancelled"
    await runtime.aclose()


@pytest.mark.asyncio
async def test_failed_discard_write_recovers_from_durable_unsuccessful_run_end(tmp_path: Path) -> None:
    async def fail_discard(_session_id: str, _run_id: str, _steer_seqs: tuple[int, ...]) -> int:
        raise RuntimeError("controlled discard failure")

    harness = make_runtime_harness(
        tmp_path,
        record_user_steers_discarded=fail_discard,
        journal_run_ended=True,
    )
    harness.runtime.start_run("session-a", "initial instruction", run_options("steer"))
    await harness.agent_loop.wait_until_started("session-a")
    await harness.runtime.submit_follow_up("session-a", "abandoned instruction", run_options())
    harness.runtime.cancel_run("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == "cancelled"

    harness.runtime.start_run("session-a", "later instruction", run_options("steer"))
    await harness.agent_loop.wait_until_started("session-a", occurrence=2)
    records = read_session_journal(harness.config.session_journal_dir, "session-a")
    later_head_seq = next(record["seq"] for record in reversed(records) if record["type"] == "user_message")

    prepared = await ReviewContext(config=harness.config).prepare("session-a", later_head_seq)

    assert all(record["type"] != "user_steers_discarded" for record in records)
    assert any(record["type"] == "run_ended" and record["status"] == "cancelled" for record in records)
    assert "abandoned instruction" not in prepared.text
    assert "later instruction" in prepared.text

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a", occurrence=2)
    await harness.runtime.aclose()
