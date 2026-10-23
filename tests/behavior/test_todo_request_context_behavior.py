import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents import Agent, SQLiteSession
from agents.testing import ScriptedModel, assistant_message, function_call

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.application.portfolios import PortfolioOperations
from investorch.context import ExecutionState
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from investorch.tools.todo import write_todos
from tests.support.config import make_test_config


@pytest.mark.parametrize("needs_approval", [False, True])
async def test_model_requests_read_current_run_todos_without_persisting_instructions(
    tmp_path: Path, needs_approval: bool
):
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Already titled")
    session = SQLiteSession("session", config.sessions_db)
    old_reasoning = {"type": "reasoning", "id": "rs_old", "summary": [], "encrypted_content": "old-opaque"}
    history = [
        {"role": "user", "content": "Earlier request"},
        old_reasoning,
        {"type": "function_call", "id": "fc_old", "call_id": "old-call", "name": "report", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "old-call", "output": "Earlier result"},
    ]
    await session.add_items(history)
    initial = [
        {"content": "Inspect the inputs", "status": "in_progress"},
        {"content": "Validate the result", "status": "pending"},
    ]
    updated = [
        {"content": "Inspect the inputs", "status": "completed"},
        {"content": "Validate the result", "status": "in_progress"},
    ]
    model = ScriptedModel(
        (
            (function_call("write_todos", {"todos": initial}, call_id="plan"),),
            (function_call("write_todos", {"todos": updated}, call_id="advance"),),
            (function_call("write_todos", {"todos": []}, call_id="clear"),),
            (assistant_message("done"),),
        )
    )
    instructions = "Follow the current user request."
    main = Agent(
        name="Main",
        instructions=instructions,
        model=model,
        tools=[replace(write_todos, needs_approval=needs_approval)],
    )
    unused = Agent(name="Unused", model=ScriptedModel())
    selected_model = model
    loop = AgentLoop(main, unused, unused, config, PortfolioOperations(config=config), lambda _: selected_model)
    approvals = []
    todo_updates = []

    async def approve(*args):
        approvals.append(args)
        return ApprovalOutcome(True, TokenUsage())

    async def output(_):
        pass

    async def update_todos(todos):
        todo_updates.append(todos)

    async def run(run_id):
        return await loop.run(
            "Continue",
            session,
            ExecutionState(),
            run_id=run_id,
            session_id="session",
            model_config=config.agent_model("main"),
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", run_id, lambda: None),
            todo_update_handler=update_todos,
        )

    try:
        assert (await run("first")).output == "done"
        model.assert_complete()
        assert len(approvals) == (3 if needs_approval else 0)
        assert todo_updates == [initial, updated, []]
        assert all(call.system_instructions == instructions for call in model.calls)
        assert not any(item.get("role") == "developer" for item in model.calls[0].input)
        for call, expected in zip(model.calls[1:3], [initial, updated], strict=True):
            note = call.input[-1]
            assert note["role"] == "developer"
            assert json.loads(note["content"].rsplit("\n", 1)[1]) == expected
            assert len([item for item in call.input if item.get("role") == "developer"]) == 1
            assert call.input[-2]["type"] == "function_call_output"
        assert not any(item.get("role") == "developer" for item in model.calls[3].input)
        request_note = model.calls[1].input[-1]["content"].splitlines()[-2]
        for call in model.calls:
            assert not any(item.get("type") == "reasoning" for item in call.input)
            assert "id" not in next(item for item in call.input if item.get("call_id") == "old-call")
        persisted = await session.get_items()
        assert old_reasoning in persisted
        assert not any(item.get("role") == "developer" for item in persisted)
        assert request_note not in json.dumps(persisted)

        selected_model = ScriptedModel(((assistant_message("new run"),),))
        assert (await run("next")).output == "new run"
        selected_model.assert_complete()
        assert selected_model.calls[0].system_instructions == instructions
        assert not any(item.get("role") == "developer" for item in selected_model.calls[0].input)
        assert request_note not in json.dumps(await session.get_items())
    finally:
        session.close()
