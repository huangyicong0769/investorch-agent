from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import ModelSettings
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseTextDeltaEvent,
)

from investorch.application import ApplicationCallbacks, open_application_host
from investorch.journal import read_session_journal
from investorch.output import AssistantMessage, AssistantMessageUpdate, ToolCalled
from investorch.storage import create_session, get_session_title, set_session_title
from investorch.web.events import WebEventBridge
from tests.support.config import make_test_config
from tests.support.model_connections import install_host_models
from tests.support.runtime import run_options


def response_with_text(text: str) -> Response:
    return Response(
        id="response",
        created_at=0,
        model="streaming-test",
        object="response",
        output=[assistant_message(text)],
        tool_choice="none",
        tools=[],
        parallel_tool_calls=False,
        status="completed",
    )


def text_delta(text: str, sequence: int = 1) -> ResponseTextDeltaEvent:
    return ResponseTextDeltaEvent(
        type="response.output_text.delta",
        item_id="scripted-message",
        output_index=0,
        content_index=0,
        delta=text,
        logprobs=[],
        sequence_number=sequence,
    )


def streaming_config(tmp_path: Path):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Streaming")
    return config


async def approve(_request, _reason):
    return True


async def test_first_snapshot_is_live_before_completion_and_only_complete_text_is_journaled(
    tmp_path: Path, monkeypatch
):
    config = streaming_config(tmp_path)
    first_seen, second_seen, next_delta, finish = (asyncio.Event() for _ in range(4))
    payloads = []
    bridge = WebEventBridge(SimpleNamespace(publish=payloads.append))

    async def stream(_call):
        response = response_with_text("Hello world")
        yield ResponseCreatedEvent(
            type="response.created",
            response=response.model_copy(update={"output": [], "status": "in_progress"}),
            sequence_number=0,
        )
        yield text_delta("Hello")
        await next_delta.wait()
        yield text_delta(" world", 2)
        await finish.wait()
        yield ResponseCompletedEvent(type="response.completed", response=response, sequence_number=3)

    model = ScriptedModel((ModelStep.stream(stream),))
    install_host_models(
        monkeypatch,
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def capture(output, seq):
        await bridge.handle_output(output, seq)
        if isinstance(output.event, AssistantMessageUpdate):
            (first_seen if output.event.text == "Hello" else second_seen).set()

    async with open_application_host(
        config,
        manual_approval_handler=approve,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        run = host.runtime.start_run("session", "Start", run_options())
        try:
            await asyncio.wait_for(first_seen.wait(), 3)
            assert not run.task.done()
            assert payloads[-1]["event"]["text"] == "Hello"
            assert payloads[-1]["journal_seq"] is None
            assert not any(
                record["type"].startswith("assistant_message")
                for record in read_session_journal(config.session_journal_dir, "session")
            )
            next_delta.set()
            await asyncio.wait_for(second_seen.wait(), 3)
            assert payloads[-1]["event"]["text"] == "Hello world"
            finish.set()
            result = await asyncio.wait_for(run.task, 3)
            assert result.output == "Hello world"
            messages = [payload for payload in payloads if payload["event"]["type"] == "assistant_message"]
            updates = [payload for payload in payloads if payload["event"]["type"] == "assistant_message_update"]
            assert [message["event"]["text"] for message in messages] == ["Hello world"]
            assert messages[0]["journal_seq"] is not None
            assert {item["event"]["message_id"] for item in (*updates, *messages)} == {
                messages[0]["event"]["message_id"]
            }
            records = read_session_journal(config.session_journal_dir, "session")
            assert [record["text"] for record in records if record["type"] == "assistant_message"] == ["Hello world"]
            assert all(record["type"] != "assistant_message_update" for record in records)
        finally:
            next_delta.set()
            finish.set()


async def test_middle_message_precedes_tool_approval_and_resume_does_not_repeat_text(tmp_path: Path, monkeypatch):
    config = streaming_config(tmp_path)
    approval_seen, release = asyncio.Event(), asyncio.Event()
    events = []
    model = ScriptedModel(
        (
            (
                assistant_message("I will create the file."),
                function_call(
                    "edit", {"path": "sample.txt", "operation": "create", "content": "created"}, call_id="edit"
                ),
            ),
            (assistant_message("Created it."),),
        )
    )
    install_host_models(
        monkeypatch,
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def manual(_request, _reason):
        approval_seen.set()
        await release.wait()
        return True

    async def capture(output, _seq):
        events.append(output.event)

    async with open_application_host(
        config,
        manual_approval_handler=manual,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        run = host.runtime.start_run("session", "Create a file", run_options())
        try:
            await asyncio.wait_for(approval_seen.wait(), 3)
            middle = next(event for event in events if isinstance(event, AssistantMessage))
            tool = next(event for event in events if isinstance(event, ToolCalled))
            assert middle.text == "I will create the file."
            assert events.index(middle) < events.index(tool)
            assert not (config.workspace_dir / "sample.txt").exists()
            release.set()
            await asyncio.wait_for(run.task, 3)
            messages = [event for event in events if isinstance(event, AssistantMessage)]
            assert [message.text for message in messages] == ["I will create the file.", "Created it."]
            assert len({message.message_id for message in messages}) == 2
            for message in messages:
                assert any(
                    isinstance(event, AssistantMessageUpdate)
                    and event.message_id == message.message_id
                    and event.text == message.text
                    for event in events
                )
            assert (config.workspace_dir / "sample.txt").read_text() == "created"
            model.assert_complete()
        finally:
            release.set()


@pytest.mark.parametrize("outcome", ["cancelled", "failed"])
async def test_interrupted_stream_keeps_only_received_text_once_with_run_outcome(tmp_path: Path, monkeypatch, outcome):
    config = streaming_config(tmp_path)
    first_seen, release = asyncio.Event(), asyncio.Event()
    events = []

    async def stream(_call):
        yield text_delta("The part already shown")
        await release.wait()
        raise RuntimeError("controlled provider failure")

    model = ScriptedModel((ModelStep.stream(stream),))
    install_host_models(
        monkeypatch,
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def capture(output, _seq):
        events.append(output.event)
        if isinstance(output.event, AssistantMessageUpdate):
            first_seen.set()

    async with open_application_host(
        config,
        manual_approval_handler=approve,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        run = host.runtime.start_run("session", "Start", run_options())
        try:
            await asyncio.wait_for(first_seen.wait(), 3)
            if outcome == "cancelled":
                host.runtime.cancel_run("session")
            else:
                release.set()
            with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else RuntimeError):
                await asyncio.wait_for(run.task, 3)
            messages = [event for event in events if isinstance(event, AssistantMessage)]
            assert [message.text for message in messages] == ["The part already shown"]
            update = next(event for event in events if isinstance(event, AssistantMessageUpdate))
            assert messages[0].message_id == update.message_id
            records = read_session_journal(config.session_journal_dir, "session")
            assert [record["text"] for record in records if record["type"] == "assistant_message"] == [update.text]
            assert all(record["type"] != "assistant_message_update" for record in records)
            assert records[-1]["type"] == "run_ended" and records[-1]["status"] == outcome
        finally:
            release.set()


async def test_complete_response_is_visible_while_title_generation_is_still_waiting(tmp_path: Path, monkeypatch):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    title_started, release_title = asyncio.Event(), asyncio.Event()
    events = []

    async def title(_call):
        title_started.set()
        await release_title.wait()
        return (assistant_message("A title"),)

    main = ScriptedModel(((assistant_message("Visible answer"),),))
    title_model = ScriptedModel((ModelStep.respond(title),))
    install_host_models(
        monkeypatch,
        lambda _config, role: (
            main if role == "main" else title_model if role == "title" else ScriptedModel(),
            ModelSettings(),
        ),
    )

    async def capture(output, _seq):
        events.append(output.event)

    async with open_application_host(
        config,
        manual_approval_handler=approve,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        session_id = await host.sessions.create()
        run = host.runtime.start_run(session_id, "Start", run_options())
        try:
            await asyncio.wait_for(title_started.wait(), 3)
            assert not run.task.done()
            assert [event.text for event in events if isinstance(event, AssistantMessage)] == ["Visible answer"]
            release_title.set()
            await asyncio.wait_for(run.task, 3)
            assert get_session_title(config.sessions_db, session_id) == "A title"
            assert [event.text for event in events if isinstance(event, AssistantMessage)] == ["Visible answer"]
        finally:
            release_title.set()


async def test_generated_image_is_preserved_without_repeating_streamed_caption(tmp_path: Path, monkeypatch):
    config = streaming_config(tmp_path)
    events = []
    image = "iVBORw0KGgo="
    response = Response.model_validate(
        {
            **response_with_text("Here is the image.").model_dump(),
            "output": [
                {"type": "image_generation_call", "id": "image", "status": "completed", "result": image},
                assistant_message("Here is the image.").model_dump(),
            ],
        }
    )
    model = ScriptedModel(
        (
            ModelStep.stream(
                (
                    text_delta("Here is the image.").model_copy(update={"output_index": 1}),
                    ResponseCompletedEvent(type="response.completed", response=response, sequence_number=2),
                )
            ),
        )
    )
    install_host_models(
        monkeypatch,
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )

    async def capture(output, _seq):
        events.append(output.event)

    async with open_application_host(
        config,
        manual_approval_handler=approve,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        run = host.runtime.start_run("session", "Draw", run_options())
        result = await asyncio.wait_for(run.task, 3)
        assert result.output == "Here is the image."
        messages = [event for event in events if isinstance(event, AssistantMessage)]
        assert [message.text for message in messages if message.text] == [result.output]
        assert [item.image_url for message in messages for item in message.images] == [f"data:image/png;base64,{image}"]
        records = read_session_journal(config.session_journal_dir, "session")
        assert [record["text"] for record in records if record["type"] == "assistant_message" and record["text"]] == [
            result.output
        ]
        assert sum(len(record.get("images", [])) for record in records if record["type"] == "assistant_message") == 1
