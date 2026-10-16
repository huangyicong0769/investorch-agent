from __future__ import annotations

from pathlib import Path

import pytest
from agents import Agent, SQLiteSession

from investorch.agents import TokenUsage, compact_session
from investorch.storage import create_session
from tests.support.config import make_test_config


@pytest.mark.parametrize("outcome", ["too_large", "main_failed", "cancelled", "completed"])
async def test_preflight_compact_usage_stays_with_its_session_when_the_run_does_not_complete(
    tmp_path: Path, monkeypatch, outcome: str
) -> None:
    import asyncio

    from agents import ModelSettings, Usage
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    from investorch.application import ApplicationCallbacks, open_application_host
    from investorch.presentation import serialize_run_ended
    from investorch.storage import set_session_title
    from tests.support.model_connections import install_host_models
    from tests.support.runtime import run_options

    config = make_test_config(tmp_path, {"compaction": {"auto_enabled": False}})
    entered_main = asyncio.Event()

    async def wait_for_stop(_):
        entered_main.set()
        await asyncio.Event().wait()

    main = ScriptedModel(
        (
            ModelStep.raise_error(RuntimeError("controlled model failure"))
            if outcome == "main_failed"
            else ModelStep.respond(wait_for_stop)
            if outcome == "cancelled"
            else (assistant_message("done"),),
        )
    )
    compact = ScriptedModel(
        (
            ModelStep(
                output=(assistant_message("X" * 150000 if outcome == "too_large" else "Prior work summarized."),),
                usage=Usage(requests=1, input_tokens=300, output_tokens=21, total_tokens=321),
            ),
        )
    )
    install_host_models(monkeypatch, lambda _config, role: (compact if role == "compact" else main, ModelSettings()))
    ended = []

    async def approved(*_):
        return True

    async def record_ended(event):
        ended.append(event)

    async with open_application_host(
        config,
        manual_approval_handler=approved,
        callbacks=ApplicationCallbacks(handle_run_ended=record_ended),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        session_id = await host.sessions.create()
        other_id = await host.sessions.create()
        set_session_title(config.sessions_db, session_id, "Already titled")
        history = SQLiteSession(session_id, config.sessions_db)
        await history.add_items([{"role": "user", "content": "X" * 300000}])
        history.close()
        host.session_models.update(session_id, context_window_tokens=100000)
        run = host.runtime.start_run(session_id, "Continue", run_options())
        if outcome == "cancelled":
            await asyncio.wait_for(entered_main.wait(), 3)
            host.runtime.cancel_run(session_id)
            with pytest.raises(asyncio.CancelledError):
                await run.task
        elif outcome == "too_large":
            with pytest.raises(ValueError, match="still exceeds"):
                await run.task
            assert main.calls == ()
        elif outcome == "main_failed":
            with pytest.raises(RuntimeError, match="controlled model failure"):
                await run.task
        else:
            await run.task
        assert host.presentation_state.get(session_id).usage.total_tokens == 321
        assert host.presentation_state.get(other_id).usage.total_tokens == 0
        assert len(ended) == 1
        payload = serialize_run_ended(ended[0])
        assert payload["auxiliary_usage"]["total_tokens"] == 321
        assert payload["main_usage"] is None or payload["main_usage"]["total_tokens"] == 0
        compact.assert_complete()


@pytest.mark.asyncio
async def test_empty_session_compaction_is_a_no_op(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session-a")
    session = SQLiteSession("session-a", config.sessions_db)
    try:
        result = await compact_session(
            Agent(name="Unreachable Compaction Agent", instructions="Must not run for empty history."),
            session,
            config,
        )
        history = await session.get_items()
    finally:
        session.close()

    assert result.changed is False
    assert result.source_items == 0
    assert result.summary_chars == 0
    assert result.usage == TokenUsage()
    assert history == []


@pytest.mark.parametrize("summary", ["Prior work summarized.", "", "X" * 9000])
async def test_switching_to_smaller_window_compacts_before_main_or_fails_closed(tmp_path: Path, summary: str) -> None:
    from agents.testing import ScriptedModel, assistant_message

    from investorch.agents import AgentLoop, ApprovalOutcome
    from investorch.agents.compact import COMPACTED_CONTEXT_PREFIX
    from investorch.application.portfolios import PortfolioOperations
    from investorch.context import ExecutionState
    from investorch.runtime.control import RunControl
    from investorch.storage import set_session_title

    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Already titled")
    session = SQLiteSession("session", config.sessions_db)
    history = [{"role": "user", "content": "Old history " * 1000}]
    await session.add_items(history)
    main_model = ScriptedModel(((assistant_message("Main answer"),),))
    compact_model = ScriptedModel(((assistant_message(summary),),))
    main = Agent(name="Main", model=main_model)
    compact = Agent(name="Compact", model=compact_model)
    cleared_context = []
    loop = AgentLoop(
        main,
        main,
        compact,
        config,
        PortfolioOperations(config=config),
        model_factory=lambda _: main_model,
        preflight_compaction_handler=lambda session_id, result: cleared_context.append((session_id, result.changed)),
    )

    async def approve(*_):
        return ApprovalOutcome(True, TokenUsage())

    async def output(_):
        pass

    async def run():
        return await loop.run(
            "New request",
            session,
            ExecutionState(),
            run_id="run",
            session_id="session",
            model_config=config.resolve_model(config.agent_model("main"), context_window_tokens=8000),
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", "run", lambda: None),
        )

    try:
        if not summary:
            with pytest.raises(ValueError, match="empty summary"):
                await run()
            assert await session.get_items() == history
            assert main_model.calls == ()
        elif len(summary) > 8000:
            with pytest.raises(ValueError, match="still exceeds"):
                await run()
            assert main_model.calls == ()
            assert cleared_context == [("session", True)]
        else:
            result = await run()
            assert result.output == "Main answer"
            assert len(compact_model.calls) == 1
            assert compact_model.calls[0].input == history
            incoming = main_model.calls[0].input
            assert incoming[0]["content"] == COMPACTED_CONTEXT_PREFIX + summary
            assert incoming[1]["content"] == [{"type": "input_text", "text": "New request"}]
            assert len(incoming) == 2
            assert cleared_context == [("session", True)]
    finally:
        session.close()


async def test_preflight_handles_the_real_main_agent_tool_schemas(tmp_path: Path) -> None:
    from agents import ModelSettings
    from agents.testing import ScriptedModel, assistant_message

    from investorch.agents import AgentLoop, ApprovalOutcome, create_agent
    from investorch.application.portfolios import PortfolioOperations
    from investorch.context import ExecutionState
    from investorch.runtime.control import RunControl
    from investorch.storage import set_session_title

    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Already titled")
    session = SQLiteSession("session", config.sessions_db)
    await session.add_items([{"role": "user", "content": "Old history " * 30000}])
    model = ScriptedModel(((assistant_message("done"),),))
    compact_model = ScriptedModel(((assistant_message("Old work summarized"),),))
    agent = create_agent(model, ModelSettings(), config)
    compact = Agent(name="Compact", model=compact_model)
    loop = AgentLoop(agent, compact, compact, config, PortfolioOperations(config=config), lambda _: model)

    async def approve(*_):
        return ApprovalOutcome(True, TokenUsage())

    async def output(_):
        pass

    try:
        result = await loop.run(
            "Continue",
            session,
            ExecutionState(),
            run_id="run",
            session_id="session",
            model_config=config.resolve_model(config.agent_model("main"), context_window_tokens=200000),
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", "run", lambda: None),
        )
        assert result.output == "done"
        assert len(compact_model.calls) == 1
        assert any(tool.name == "update_config" for tool in model.calls[0].tools)
        assert len(model.calls[0].input) == 2
    finally:
        session.close()
