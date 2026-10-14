import asyncio

import pytest
from agents import ModelSettings, SQLiteSession
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.application import open_application_host
from investorch.application.review_context import ReviewContext
from investorch.application.session_queries import read_session
from investorch.runtime import RunCapacityExceededError
from investorch.storage import create_session, list_sessions, set_session_title
from tests.support.config import make_test_config
from tests.support.runtime import run_options


@pytest.mark.parametrize("approved", [False, True])
async def test_sdk_spawn_requires_permission_and_uses_the_normal_child_agent(tmp_path, monkeypatch, approved):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    create_session(config.sessions_db, "parent")
    set_session_title(config.sessions_db, "parent", "Parent")
    approval_pending, release_approval = asyncio.Event(), asyncio.Event()
    child_started, release_child = asyncio.Event(), asyncio.Event()
    approvals, child_inputs, child_tools = [], [], []

    async def respond(call):
        if any(item.get("type") == "function_call" for item in call.input):
            return (assistant_message("delegated"),)
        if any(item.get("role") == "user" and "parent task" in str(item) for item in call.input):
            return (function_call("spawn_agent", {"task": "child research", "title": "Child"}, call_id="spawn"),)
        child_inputs.append(call.input)
        child_tools.append({tool.name for tool in call.tools})
        child_started.set()
        await release_child.wait()
        return (assistant_message("child complete"),)

    model = ScriptedModel(tuple(ModelStep.respond(respond) for _ in range(3 if approved else 2)))
    monkeypatch.setattr(
        "investorch.application.host.create_model",
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def manual(request, _reason):
        approvals.append(request)
        approval_pending.set()
        await release_approval.wait()
        return approved

    async with open_application_host(
        config, manual_approval_handler=manual, create_initial_session=False, enable_activity=False
    ) as host:
        parent = host.runtime.start_run("parent", "parent task", run_options())
        try:
            await asyncio.wait_for(approval_pending.wait(), 3)
            assert [record.session_id for record in list_sessions(config.sessions_db)] == ["parent"]
            assert approvals[0].tool_name == "spawn_agent"
            assert approvals[0].session_id == "parent" and approvals[0].run_id == parent.run_id
            release_approval.set()
            await asyncio.wait_for(parent.task, 3)
            children = [record for record in list_sessions(config.sessions_db) if record.session_id != "parent"]
            if not approved:
                assert children == [] and not child_started.is_set()
            else:
                await asyncio.wait_for(child_started.wait(), 3)
                assert len(children) == 1
                assert child_tools[0] == {tool.name for tool in model.calls[0].tools}
                assert "spawn_agent" in child_tools[0] and "send_message" in child_tools[0]
                child = children[0]
                assert host.runtime.is_session_active(child.session_id)
                assert child.title == "Child" and child.spawned_by_run_id == parent.run_id
                record = (await read_session(child.session_id, sessions_db=config.sessions_db, journal=host.journal))[
                    "records"
                ][0]
                assert record["text"] == "child research" and record["type"] == "user"
                assert record["origin"] == {
                    "kind": "agent",
                    "sender_session_id": "parent",
                    "sender_run_id": parent.run_id,
                }
                assert not any("parent task" in str(item) for item in child_inputs[0])
                assert any(item.get("role") == "user" and "child research" in str(item) for item in child_inputs[0])
                assert not any(
                    item.get("role") == "developer" and "child research" in str(item) for item in child_inputs[0]
                )
                review = await ReviewContext(config=config).prepare(child.session_id, record["seq"])
                assert "child research" in review.text and parent.run_id in review.text
                active_child = host.runtime.get_active_run(child.session_id)
                release_child.set()
                await asyncio.wait_for(active_child.task, 3)
                sdk = SQLiteSession(child.session_id, config.sessions_db)
                try:
                    assert any(item.get("role") == "user" for item in await sdk.get_items())
                finally:
                    sdk.close()
            model.assert_complete()
        finally:
            release_approval.set()
            release_child.set()


async def test_waiting_approval_and_stopping_runs_keep_their_capacity_slot(tmp_path, monkeypatch):
    config = make_test_config(
        tmp_path,
        {
            "runtime": {"max_concurrent_runs": 1},
            "backtest": {"use_cnequity": False},
            "compaction": {"auto_enabled": False},
        },
    )
    for session in ("parent", "other"):
        create_session(config.sessions_db, session)
        set_session_title(config.sessions_db, session, session)
    pending = asyncio.Event()
    model = ScriptedModel(
        (
            (function_call("spawn_agent", {"task": "child", "title": None}, call_id="spawn"),),
            (assistant_message("other complete"),),
        )
    )
    monkeypatch.setattr(
        "investorch.application.host.create_model",
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def approve(_request, _reason):
        pending.set()
        await asyncio.Event().wait()

    async with open_application_host(
        config, manual_approval_handler=approve, create_initial_session=False, enable_activity=False
    ) as host:
        parent = host.runtime.start_run("parent", "delegate", run_options())
        await asyncio.wait_for(pending.wait(), 3)
        assert host.runtime.session_snapshot("parent").run_phase == "waiting_approval"
        with pytest.raises(RunCapacityExceededError):
            host.runtime.start_run("other", "new", run_options())
        host.runtime.cancel_run("parent")
        assert host.runtime.session_snapshot("parent").run_phase == "stopping"
        with pytest.raises(RunCapacityExceededError):
            host.runtime.start_run("other", "new", run_options())
        with pytest.raises(asyncio.CancelledError):
            await parent.task
        other = host.runtime.start_run("other", "new", run_options())
        await other.task
        assert len(list_sessions(config.sessions_db)) == 2
        model.assert_complete()
