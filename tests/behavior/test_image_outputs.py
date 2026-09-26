from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import Agent, RunConfig, Runner, ToolOutputImage, ToolOutputText, function_tool
from agents.testing import ScriptedModel, assistant_message, function_call

from investorch.images import ImageContent
from investorch.journal import SessionJournal, read_session_journal
from investorch.output import AssistantMessage, ToolOutput, consume_run_events
from investorch.ui.console import ConsoleRenderer, ConsoleUI


@pytest.mark.asyncio
async def test_mixed_sdk_image_output_survives_stream_journal_and_console(tmp_path: Path, capsys):
    url = "data:image/png;base64,iVBORw0KGgo="

    @function_tool
    def inspect_picture():
        return [ToolOutputText(text="Chart observation"), ToolOutputImage(image_url=url)]

    model = ScriptedModel(((function_call("inspect_picture", {}, call_id="image-read"),), (assistant_message("Done"),)))
    result = Runner.run_streamed(
        Agent(name="Reader", model=model, tools=[inspect_picture]),
        "Inspect",
        run_config=RunConfig(tracing_disabled=True),
    )
    events = []
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    renderer = ConsoleRenderer(ConsoleUI())

    async def record(event):
        events.append(event)
        await journal.record_output("session", event)
        await renderer.handle(event)

    await consume_run_events(result, record, "Reader")
    output = next(event for event in events if isinstance(event, ToolOutput))
    assert output.output == "Chart observation"
    assert output.images[0].image_url == url
    record = next(record for record in read_session_journal(tmp_path, "session") if record["type"] == "tool_output")
    assert record["images"][0]["image_url"] == url
    displayed = capsys.readouterr().out
    assert "Chart observation" in displayed and "[image:" in displayed
    assert "base64" not in displayed


@pytest.mark.asyncio
async def test_image_only_assistant_persists_and_prints_without_payload(tmp_path: Path, capsys):
    image = ImageContent("https://images.example.com/chart.svg", filename="chart.svg", media_type="image/svg+xml")
    event = AssistantMessage(text="", images=(image,))
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    await journal.record_output("session", event)
    record = read_session_journal(tmp_path, "session")[0]
    assert record["text"] == ""
    assert record["images"][0]["image_url"] == image.image_url
    await ConsoleRenderer(ConsoleUI()).handle(event)
    assert "[external image: images.example.com]" in capsys.readouterr().out
