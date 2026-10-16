import asyncio
import base64
from pathlib import Path

import pytest
from agents import ModelSettings
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.application import ApplicationCallbacks, open_application_host
from investorch.application.messaging import send_message
from investorch.application.session_queries import read_session
from investorch.images import ImageContent
from investorch.inputs import UserInput
from investorch.output import ToolOutput
from investorch.runtime import RunDefaults
from investorch.storage import create_session, set_session_title
from tests.support.config import make_test_config
from tests.support.model_connections import install_host_models
from tests.support.runtime import run_options


@pytest.mark.parametrize("approved", [False, True, "cancel"])
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

    steps = 1 if approved == "cancel" else 3 if approved else 2
    model = ScriptedModel(tuple(ModelStep.respond(respond) for _ in range(steps)))
    install_host_models(
        monkeypatch,
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
            if approved == "cancel":
                host.runtime.cancel_run("a")
                with pytest.raises(asyncio.CancelledError):
                    await sender.task
                assert not receiver_started.is_set()
                assert host.runtime.is_session_active("b") is False
                assert (await read_session("b", sessions_db=config.sessions_db, journal=host.journal))["records"] == []
                model.assert_complete()
                return
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
    install_host_models(
        monkeypatch,
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


async def test_active_agent_steer_reaches_sdk_and_frozen_permission_review_in_the_same_run(tmp_path, monkeypatch):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    for session_id in ("sender", "target"):
        create_session(config.sessions_db, session_id)
        set_session_title(config.sessions_db, session_id, session_id)
    started, release = asyncio.Event(), asyncio.Event()

    async def first(_call):
        started.set()
        await release.wait()
        return (function_call("calculate", {"expression": "1+1"}, call_id="calculate"),)

    model = ScriptedModel(
        (
            ModelStep.respond(first),
            (
                function_call(
                    "edit", {"path": "reviewed.txt", "operation": "create", "content": "approved"}, call_id="edit"
                ),
            ),
            (assistant_message("done"),),
        )
    )
    permission = ScriptedModel(((assistant_message('{"decision":"approve","reason":"effective instruction"}'),),))

    def model_for_role(_config, role):
        return (model if role == "main" else permission if role == "permission" else ScriptedModel()), ModelSettings()

    install_host_models(monkeypatch, model_for_role)

    async def no_manual(_request, _reason):
        raise AssertionError("The existing Permission model approved this Tool")

    async with open_application_host(
        config, manual_approval_handler=no_manual, create_initial_session=False, enable_activity=False
    ) as host:
        active = host.runtime.start_run("target", "human instruction", RunDefaults("review", "steer"))
        await asyncio.wait_for(started.wait(), 3)
        receipt = await send_message(
            state=host.state,
            runtime=host.runtime,
            journal=host.journal,
            source_session_id="sender",
            source_run_id="sender-run",
            target_session_id="target",
            text="write reviewed.txt",
        )
        assert receipt.disposition == "steer_submitted"
        release.set()
        await asyncio.wait_for(active.task, 3)
        raw = await read_session("target", sessions_db=config.sessions_db, journal=host.journal, view="events")
        activation = next(record for record in raw["records"] if record["type"] == "user_steers_activated")
        approval = next(record for record in raw["records"] if record["type"] == "approval")
        assert activation["run_id"] == approval["run_id"] == active.run_id
        assert approval["instruction_head_seq"] == activation["seq"]
        assert approval["source"] == "permission" and approval["approved"] is True
        assert (config.workspace_dir / "reviewed.txt").read_text() == "approved"
        assert sum(record["type"] == "run_ended" for record in raw["records"]) == 1
        sdk_users = [item for item in model.calls[1].input if item.get("role") == "user"]
        assert sdk_users[-1]["content"][1] == {"type": "input_text", "text": "write reviewed.txt"}
        assert 'Session: "sender"' in sdk_users[-1]["content"][0]["text"]
        evidence = str(permission.calls[0].input)
        assert "origin=agent" in evidence and "sender-run" in evidence and "write reviewed.txt" in evidence
        model.assert_complete()
        permission.assert_complete()


@pytest.mark.parametrize("source", ["workspace", "session_event"])
async def test_approved_image_reference_reaches_recipient_sdk_and_does_not_approve_its_tools(
    tmp_path, monkeypatch, source
):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    for session_id in ("a", "b"):
        create_session(config.sessions_db, session_id)
        set_session_title(config.sessions_db, session_id, session_id)
    encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="
    image = ImageContent(
        f"data:image/png;base64,{encoded}", detail="high", filename="chart.png", media_type="image/png"
    )
    (config.workspace_dir / "chart.png").write_bytes(base64.b64decode(encoded))
    images = (
        [{"source": "workspace", "path": "chart.png", "detail": "high"}]
        if source == "workspace"
        else [{"source": "session_event", "seq": 1, "image_index": 0}]
    )
    text = "inspect chart" if source == "workspace" else ""
    receiver_done = asyncio.Event()
    receiver_inputs, approvals, permission_evidence = [], [], []

    async def respond(call):
        if any(item.get("type") == "function_call" for item in call.input):
            return (assistant_message("done"),)
        if any(item.get("role") == "user" and "send image" in str(item) for item in call.input):
            return (
                function_call(
                    "send_message", {"target_session_id": "b", "text": text, "images": images}, call_id="image-send"
                ),
            )
        receiver_inputs.append(call.input)
        return (
            function_call(
                "edit",
                {"path": "must-not-write.txt", "operation": "create", "content": "denied"},
                call_id="receiver-edit",
            ),
        )

    model = ScriptedModel(tuple(ModelStep.respond(respond) for _ in range(4)))
    install_host_models(
        monkeypatch,
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def manual(request, _reason):
        approvals.append(request)
        if request.session_id == "b":
            from investorch.application.review_context import ReviewContext

            permission_evidence.append(await ReviewContext(config=config).prepare("b", request.instruction_head_seq))
        return request.session_id == "a"

    async def ended(event):
        if event.session_id == "b":
            receiver_done.set()

    async with open_application_host(
        config,
        manual_approval_handler=manual,
        callbacks=ApplicationCallbacks(handle_run_ended=ended),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        initial = UserInput("send image", (image,)) if source == "session_event" else UserInput("send image")
        sender = host.runtime.start_run("a", initial, run_options())
        await asyncio.wait_for(sender.task, 3)
        await asyncio.wait_for(receiver_done.wait(), 3)
        assert [(request.session_id, request.tool_name) for request in approvals] == [
            ("a", "send_message"),
            ("b", "edit"),
        ]
        assert not (config.workspace_dir / "must-not-write.txt").exists()
        assert 'Session "a"' in permission_evidence[0].text and sender.run_id in permission_evidence[0].text
        content = next(item["content"] for item in receiver_inputs[0] if item.get("role") == "user")
        assert content[0] == {
            "type": "input_text",
            "text": f'Message sent by another InvestOrch Session: "a" (Run "{sender.run_id}").',
        }
        assert content[-1] == {"type": "input_image", "image_url": image.image_url, "detail": "high"}
        assert len(content) == (3 if text else 2)
        if text:
            assert content[1] == {"type": "input_text", "text": text}
        page = await read_session("b", sessions_db=config.sessions_db, journal=host.journal)
        record = next(record for record in page["records"] if record["type"] == "user")
        assert record["text"] == text
        assert record["images"] == [{"filename": "chart.png", "media_type": "image/png", "detail": "high"}]
        assert "image_url" not in record["images"][0]
        model.assert_complete()
