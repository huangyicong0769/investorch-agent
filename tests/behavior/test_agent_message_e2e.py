import asyncio
from pathlib import Path

import pytest
from agents import ModelSettings
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.application import ApplicationCallbacks, open_application_host
from investorch.application.session_queries import read_session
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

    async with open_application_host(
        config,
        manual_approval_handler=manual,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        sender = host.runtime.start_run("a", "sender task", run_options())
        try:
            await asyncio.wait_for(approval_pending.wait(), 3)
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
