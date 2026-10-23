import asyncio
import json
from pathlib import Path

import pytest
from agents import Agent, SQLiteSession, Usage, function_tool
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.agents.compact import COMPACTED_CONTEXT_PREFIX
from investorch.application.portfolios import PortfolioOperations
from investorch.context import ExecutionState
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from tests.support.config import make_test_config
from tests.support.runtime import run_options


def canonical(items):
    return [{key: value for key, value in item.items() if key != "id"} for item in items]


def assert_tool_pairs(items) -> None:
    calls = set()
    for item in items:
        if item.get("type") == "function_call":
            calls.add(item["call_id"])
        elif item.get("type") == "function_call_output":
            assert item["call_id"] in calls
            calls.remove(item["call_id"])
    assert not calls


@pytest.mark.parametrize("mode", ["completed", "approval", "steer", "failed", "cancelled"])
async def test_running_projection_reaches_sdk_and_flushes_without_repeating_tools(tmp_path: Path, mode: str) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Already titled")
    session = SQLiteSession("session", config.sessions_db)
    control = RunControl("session", "run", lambda: None)
    executions = []
    approvals = []
    usages = []
    contexts = []
    activations = []
    entered_wait = asyncio.Event()
    successful = mode not in {"failed", "cancelled"}
    count = 3 if successful else 1

    @function_tool(needs_approval=mode == "approval")
    def large_result(number: int) -> str:
        """Return a large report for this numbered step."""
        executions.append(number)
        return f"report-{number}:" + "x" * 12000

    async def wait_for_cancellation(_call):
        entered_wait.set()
        await asyncio.Event().wait()

    steps = [
        (function_call("large_result", json.dumps({"number": index}), call_id=f"report-{index}"),)
        for index in range(count)
    ]
    steps.append(
        ModelStep.raise_error(RuntimeError("request after compact failed"))
        if mode == "failed"
        else ModelStep.respond(wait_for_cancellation)
        if mode == "cancelled"
        else (assistant_message("Final verified result"),)
    )
    main = ScriptedModel(steps, default_usage=Usage(requests=1, input_tokens=20, output_tokens=10, total_tokens=30))
    compact = ScriptedModel(
        [(assistant_message(f"Summary of completed work {index}"),) for index in range(count)],
        default_usage=Usage(requests=1, input_tokens=30, output_tokens=7, total_tokens=37),
    )

    async def on_tool(*_args):
        if mode == "steer" and len(executions) == 1:
            steer = control.reserve_steer("Keep the added constraint exactly", run_options())
            control.mark_ready(steer.steer_id, journal_seq=2)

    async def activate(sequences):
        activations.append(sequences)
        return 3, None

    async def approve(_head, name, arguments):
        approvals.append((name, arguments))
        return ApprovalOutcome(True, TokenUsage())

    async def output(_event):
        pass

    agent = Agent(name="Main", model=main, tools=[large_result])
    loop = AgentLoop(
        agent,
        agent,
        Agent(name="Compact", model=compact),
        config,
        PortfolioOperations(config=config),
        model_factory=lambda _: main,
        successful_tool_handler=on_tool,
        usage_handler=lambda _session, usage, model: usages.append((usage, model is not None)),
        context_handler=lambda _session, tokens, _model, **kwargs: contexts.append((tokens, kwargs.get("compacting"))),
    )
    task = asyncio.create_task(
        loop.run(
            "Preserve this current user directive exactly",
            session,
            ExecutionState(),
            run_id="run",
            session_id="session",
            model_config=config.resolve_model(config.agent_model("main"), context_window_tokens=8000),
            approval_handler=approve,
            output_handler=output,
            run_control=control,
            steer_activated_handler=activate,
        )
    )
    try:
        if mode == "failed":
            with pytest.raises(RuntimeError, match="request after compact failed"):
                await task
        elif mode == "cancelled":
            await asyncio.wait_for(entered_wait.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result.output == "Final verified result"
            assert result.main_usage.total_tokens == (count + 1) * 30
            assert result.auxiliary_usage.total_tokens == count * 37

        assert executions == list(range(count))
        assert len(approvals) == (count if mode == "approval" else 0)
        assert len(compact.calls) == count
        assert len(main.calls) == count + 1
        assert sum(usage.total_tokens for usage, is_main in usages if is_main) == (count + int(successful)) * 30
        assert sum(usage.total_tokens for usage, is_main in usages if not is_main) == count * 37
        assert sum(compacting is True for _tokens, compacting in contexts) == count
        for index, call in enumerate(main.calls[1:]):
            incoming = call.input
            summary_index = next(
                position
                for position, item in enumerate(incoming)
                if isinstance(item.get("content"), str) and item["content"].startswith(COMPACTED_CONTEXT_PREFIX)
            )
            assert incoming[summary_index]["content"] == COMPACTED_CONTEXT_PREFIX + f"Summary of completed work {index}"
            assert all(
                position < summary_index
                for position, item in enumerate(incoming)
                if item.get("role") in {"user", "developer", "system"}
            )
            assert "Preserve this current user directive exactly" in json.dumps(incoming)
            assert len(json.dumps(incoming)) < 3000
            assert "x" * 12000 not in json.dumps(incoming)
            assert_tool_pairs(incoming)
        if mode == "steer":
            assert activations == [(2,)]
            assert all("Keep the added constraint exactly" in json.dumps(call.input) for call in main.calls[1:])
        history = await session.get_items()
        sent_prefix = main.calls[-1].input
        assert canonical(history[: len(sent_prefix)]) == canonical(sent_prefix)
        assert_tool_pairs(history)
        assert len(json.dumps(history)) < 3000
        if successful:
            assert history[-1]["content"][0]["text"] == "Final verified result"
        main.assert_complete()
        compact.assert_complete()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        session.close()


async def test_measured_responses_anchor_only_new_content_within_and_between_runs(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Already titled")
    session = SQLiteSession("session", config.sessions_db)
    executions = []

    @function_tool
    def read_evidence(number: int) -> str:
        """Read new evidence for this step."""
        executions.append(number)
        return "new evidence " * 200

    main = ScriptedModel(
        (
            ModelStep(
                output=(function_call("read_evidence", {"number": 1}, call_id="first"),),
                usage=Usage(requests=1, input_tokens=1200, output_tokens=100, total_tokens=1300),
            ),
            ModelStep(
                output=(function_call("read_evidence", {"number": 2}, call_id="second"),),
                usage=Usage(requests=1, input_tokens=1800, output_tokens=100, total_tokens=1900),
            ),
            ModelStep(
                output=(assistant_message("Evidence verified"),),
                usage=Usage(requests=1, input_tokens=3000, output_tokens=100, total_tokens=3100),
            ),
            ModelStep(
                output=(assistant_message("Follow-up verified"),),
                usage=Usage(requests=1, input_tokens=3600, output_tokens=100, total_tokens=3700),
            ),
        )
    )
    compact = ScriptedModel()
    agent = Agent(name="Main", instructions="Established instructions. " * 140, model=main, tools=[read_evidence])
    loop = AgentLoop(
        agent,
        agent,
        Agent(name="Compact", model=compact),
        config,
        PortfolioOperations(config=config),
        model_factory=lambda _: main,
    )

    async def approve(*_):
        return ApprovalOutcome(True, TokenUsage())

    async def output(_):
        pass

    async def run(run_id):
        return await loop.run(
            "Continue the requested work",
            session,
            ExecutionState(),
            run_id=run_id,
            session_id="session",
            model_config=config.resolve_model(config.agent_model("main"), context_window_tokens=8000),
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", run_id, lambda: None),
        )

    try:
        assert (await run("first")).output == "Evidence verified"
        assert (await run("follow-up")).output == "Follow-up verified"
        assert executions == [1, 2]
        assert len(main.calls) == 4
        assert not compact.calls
        assert len(await session.get_items()) > 6
        main.assert_complete()
    finally:
        session.close()
