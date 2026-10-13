import asyncio
from pathlib import Path

import pytest
from agents import ModelSettings
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.application import ApplicationCallbacks, open_application_host
from investorch.application.messaging import send_message
from investorch.application.session_queries import read_session
from investorch.images import UserInput
from investorch.output import ToolOutput
from investorch.storage import create_session, set_session_title
from tests.support.config import make_test_config
from tests.support.runtime import run_options


@pytest.mark.parametrize("approved", [False, True])
async def test_sender_sdk_approval_controls_delivery_without_waiting_for_receiver(
    tmp_path: Path, monkeypatch, approved
):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    for session_id in ("a", "b"):
        create_session(config.sessions_db, session_id)
        set_session_title(config.sessions_db, session_id, session_id)
    approval_pending = asyncio.Event()
    release_approval = asyncio.Event()
    receiver_started = asyncio.Event()
    release_receiver = asyncio.Event()
    approvals, outputs, receiver_inputs = [], [], []
    committed = []

    async def respond(call):
        if not any(item.get("type") == "function_call" for item in call.input):
            if any(item.get("role") == "user" and "sender task" in str(item) for item in call.input):
                return (
                    function_call("send_message", {"target_session_id": "b", "text": "receiver task"}, call_id="send"),
                )
            receiver_inputs.append(call.input)
            receiver_started.set()
            await release_receiver.wait()
            return (assistant_message("receiver complete"),)
        return (assistant_message("sender complete"),)

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

    async def capture(output, _seq):
        outputs.append(output)

    async def input_committed(session_id: str, user_input: UserInput, seq: int):
        visible = (await read_session(session_id, sessions_db=config.sessions_db, journal=host.journal))["records"]
        assert any(record["seq"] == seq and record["text"] == user_input.text for record in visible)
        committed.append((session_id, seq, user_input))

    async with open_application_host(
        config,
        manual_approval_handler=manual,
        callbacks=ApplicationCallbacks(handle_output=capture, handle_user_input_committed=input_committed),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        sender = host.runtime.start_run("a", "sender task", run_options())
        try:
            await asyncio.wait_for(approval_pending.wait(), 3)
            assert [item[0] for item in committed] == ["a"]
            assert approvals[0].session_id == "a"
            assert approvals[0].run_id == sender.run_id
            assert approvals[0].tool_name == "send_message"
            assert host.runtime.is_session_active("b") is False
            assert host.runtime.list_queued_inputs("b") == []
            assert (await read_session("b", sessions_db=config.sessions_db, journal=host.journal))["records"] == []
            release_approval.set()
            await asyncio.wait_for(sender.task, 3)
            if approved:
                await asyncio.wait_for(receiver_started.wait(), 3)
                assert host.runtime.is_session_active("b") is True
                receipt = next(
                    output.event.output
                    for output in outputs
                    if isinstance(output.event, ToolOutput) and output.session_id == "a"
                )
                assert "run_started" in receipt
                record = (await read_session("b", sessions_db=config.sessions_db, journal=host.journal))["records"][0]
                assert record["type"] == "user"
                assert record["text"] == "receiver task"
                assert record["origin"] == {"kind": "agent", "sender_session_id": "a", "sender_run_id": sender.run_id}
                assert [item[0] for item in committed] == ["a", "b"]
                content = next(item["content"] for item in receiver_inputs[0] if item.get("role") == "user")
                assert content[0]["type"] == "input_text"
                assert 'Session: "a"' in content[0]["text"]
                assert sender.run_id in content[0]["text"]
                assert content[1] == {"type": "input_text", "text": "receiver task"}
                receiver = host.runtime.get_active_run("b")
                release_receiver.set()
                await asyncio.wait_for(receiver.task, 3)
            else:
                assert not receiver_started.is_set()
                assert host.runtime.is_session_active("b") is False
                assert (await read_session("b", sessions_db=config.sessions_db, journal=host.journal))["records"] == []
            model.assert_complete()
        finally:
            release_approval.set()
            release_receiver.set()


@pytest.mark.parametrize("behavior", ["steer", "queue"])
async def test_real_sdk_promotes_origin_without_duplicating_target_input(tmp_path, monkeypatch, behavior):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    for session_id in ("sender", "target"):
        create_session(config.sessions_db, session_id)
        set_session_title(config.sessions_db, session_id, session_id)
    started, release, promoted_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    ended = []

    async def first(_call):
        started.set()
        await release.wait()
        return (assistant_message("first done"),)

    model = ScriptedModel((ModelStep.respond(first), (assistant_message("follow-up done"),)))
    monkeypatch.setattr(
        "investorch.application.host.create_model",
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def no_approval(_request, _reason):
        raise AssertionError("No write Tool was called")

    async def finished(event):
        ended.append(event)
        if len(ended) == 2:
            promoted_finished.set()

    async with open_application_host(
        config,
        manual_approval_handler=no_approval,
        callbacks=ApplicationCallbacks(handle_run_ended=finished),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        active = host.runtime.start_run("target", "human instruction", run_options(behavior))
        await asyncio.wait_for(started.wait(), 3)
        receipt = await send_message(
            state=host.state,
            runtime=host.runtime,
            journal=host.journal,
            source_session_id="sender",
            source_run_id="sender-run",
            target_session_id="target",
            text="Agent follow-up",
        )
        assert receipt.disposition == f"{behavior}_submitted"
        assert receipt.run_id == active.run_id
        release.set()
        await asyncio.wait_for(promoted_finished.wait(), 3)
        assert [event.status for event in ended] == ["completed", "completed"]
        page = await read_session("target", sessions_db=config.sessions_db, journal=host.journal)
        users = [record for record in page["records"] if record["type"] == "user"]
        assert [record["text"] for record in users] == ["human instruction", "Agent follow-up"]
        assert users[1]["origin"]["sender_run_id"] == "sender-run"
        sdk_users = [item for item in model.calls[1].input if item.get("role") == "user"]
        assert sdk_users[-1]["content"] == [
            {"type": "input_text", "text": 'Message sent by another InvestOrch Session: "sender" (Run "sender-run").'},
            {"type": "input_text", "text": "Agent follow-up"},
        ]
        raw = await read_session("target", sessions_db=config.sessions_db, journal=host.journal, view="events")
        assert sum(record["type"] in {"user_message", "user_steer"} for record in raw["records"]) == 2
        model.assert_complete()
