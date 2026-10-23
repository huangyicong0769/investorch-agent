from pathlib import Path

from agents import Agent, SQLiteSession, function_tool
from agents.testing import ScriptedModel, assistant_message, function_call
from openai.types.responses import ResponseReasoningItem

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.application.portfolios import PortfolioOperations
from investorch.context import ExecutionState
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from tests.support.config import make_test_config


async def test_prior_run_opaque_history_is_portable_but_current_run_reasoning_survives_approval(tmp_path: Path):
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Already titled")
    session = SQLiteSession("session", config.sessions_db)
    old_reasoning = {"type": "reasoning", "id": "rs_other_account", "summary": [], "encrypted_content": "old-opaque"}
    history = [
        {"role": "user", "content": "Earlier request"},
        old_reasoning,
        {"type": "function_call", "id": "fc_other_account", "call_id": "old-call", "name": "report", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "old-call", "output": "Earlier tool result"},
    ]
    await session.add_items(history)
    current_reasoning = ResponseReasoningItem(
        id="rs_current", type="reasoning", summary=[], encrypted_content="current-opaque"
    )
    model = ScriptedModel(
        ((current_reasoning, function_call("report", {}, call_id="current-call")), (assistant_message("done"),))
    )

    @function_tool(needs_approval=True)
    def report() -> str:
        """Report a result."""
        return "Current tool result"

    main = Agent(name="Main", model=model, tools=[report])
    unused = Agent(name="Unused", model=ScriptedModel())
    loop = AgentLoop(main, unused, unused, config, PortfolioOperations(config=config), lambda _: model)
    approvals = []

    async def approve(*args):
        approvals.append(args)
        return ApprovalOutcome(True, TokenUsage())

    async def output(_):
        pass

    async def run(selected_loop, run_id):
        return await selected_loop.run(
            "Continue",
            session,
            ExecutionState(),
            run_id=run_id,
            session_id="session",
            model_config=config.agent_model("main"),
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", run_id, lambda: None),
        )

    try:
        assert (await run(loop, "first")).output == "done"
        assert len(approvals) == 1
        first_input = model.calls[0].input
        assert not any(item.get("type") == "reasoning" for item in first_input)
        assert next(item for item in first_input if item.get("call_id") == "old-call")["name"] == "report"
        assert all("id" not in item for item in first_input)
        second_input = model.calls[1].input
        assert any(
            item.get("id") == "rs_current" and item.get("encrypted_content") == "current-opaque"
            for item in second_input
        )
        assert old_reasoning in await session.get_items()
        session.close()
        session = SQLiteSession("session", config.sessions_db)
        restarted_model = ScriptedModel(((assistant_message("after restart"),),))
        restarted_loop = AgentLoop(
            main, unused, unused, config, PortfolioOperations(config=config), lambda _: restarted_model
        )
        assert (await run(restarted_loop, "after-restart")).output == "after restart"
        assert not any(item.get("type") == "reasoning" or "id" in item for item in restarted_model.calls[0].input)
        persisted = await session.get_items()
        assert old_reasoning in persisted
        assert any(item.get("id") == "rs_current" for item in persisted)
    finally:
        session.close()


async def test_compaction_receives_portable_history_and_does_not_mutate_it_when_summary_fails(tmp_path: Path):
    import pytest

    from investorch.agents import compact_session

    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    session = SQLiteSession("session", config.sessions_db)
    history = [
        {"type": "reasoning", "id": "rs_old", "summary": [], "encrypted_content": "other-account-opaque"},
        {
            "type": "message",
            "id": "msg_old",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Old result", "annotations": []}],
        },
    ]
    await session.add_items(history)
    model = ScriptedModel(((assistant_message(""),), (assistant_message(""),)))
    try:
        with pytest.raises(ValueError, match="empty summary"):
            await compact_session(Agent(name="Compact", model=model), session, config)
        assert model.calls[0].input == [{key: value for key, value in history[1].items() if key != "id"}]
        assert await session.get_items() == history
    finally:
        session.close()
