from __future__ import annotations

from pathlib import Path

import pytest
from agents import Agent, SQLiteSession

from investorch.agents import TokenUsage, compact_session
from investorch.storage import create_session
from tests.support.config import make_test_config


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
