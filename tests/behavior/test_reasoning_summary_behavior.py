from zoneinfo import ZoneInfo

import pytest
from agents import Agent, RunConfig, Runner, function_tool
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseOutputItemDoneEvent,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
)

from investorch.journal import SessionJournal, read_session_journal
from investorch.output import Reasoning, consume_run_events


@pytest.mark.parametrize(
    "mode", ["summary_deltas", "delta_only", "summary_done", "sdk_item", "summary_only", "text_only", "empty_summary"]
)
async def test_reasoning_preserves_summary_and_body_once_across_tool_turns(tmp_path, mode):
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    captured = []

    @function_tool
    def inspect_data() -> str:
        return "Inspected"

    def model_turn(number):
        async def stream(_call):
            summary = [f"Checking inputs {number}.", f"Comparing results {number}."]
            has_summary = mode not in ("text_only", "empty_summary")
            reasoning = ResponseReasoningItem(
                id="reused-reasoning-id",
                type="reasoning",
                summary=[{"type": "summary_text", "text": text} for text in summary]
                if has_summary and mode != "delta_only"
                else [],
                content=None
                if mode == "summary_only"
                else [{"type": "reasoning_text", "text": f"Full reasoning {number}."}],
            )
            output = [
                reasoning,
                function_call("inspect_data", {}, call_id="inspect-call") if number == 1 else assistant_message("Done"),
            ]
            response = Response(
                id=f"response-{number}",
                created_at=0,
                model="test",
                object="response",
                output=output,
                tool_choice="auto",
                tools=[],
                parallel_tool_calls=False,
                status="completed",
            )
            yield ResponseCreatedEvent(
                type="response.created", response=response.model_copy(update={"output": []}), sequence_number=0
            )
            if mode not in ("sdk_item", "summary_only"):
                yield ResponseReasoningTextDeltaEvent(
                    type="response.reasoning_text.delta",
                    item_id=reasoning.id,
                    output_index=0,
                    content_index=0,
                    delta=f"Full reasoning {number}.",
                    sequence_number=1,
                )
            if mode in ("summary_deltas", "delta_only", "empty_summary"):
                for index, text in enumerate(summary if has_summary else ["   "]):
                    for part in [text[:5], text[5:]]:
                        yield ResponseReasoningSummaryTextDeltaEvent(
                            type="response.reasoning_summary_text.delta",
                            item_id=reasoning.id,
                            output_index=0,
                            summary_index=index,
                            delta=part,
                            sequence_number=2,
                        )
            if mode != "sdk_item":
                yield ResponseOutputItemDoneEvent(
                    type="response.output_item.done", item=reasoning, output_index=0, sequence_number=3
                )
            yield ResponseCompletedEvent(type="response.completed", response=response, sequence_number=4)

        return stream

    async def capture(event):
        captured.append(event)
        await journal.record_output("session", event)

    result = Runner.run_streamed(
        Agent(
            name="Main",
            tools=[inspect_data],
            model=ScriptedModel(tuple(ModelStep.stream(model_turn(n)) for n in (1, 2))),
        ),
        "Inspect",
        run_config=RunConfig(tracing_disabled=True),
    )
    await consume_run_events(result, capture, "Main")
    expected_summary = [
        f"Checking inputs {n}.\n\nComparing results {n}." if mode not in ("text_only", "empty_summary") else None
        for n in (1, 2)
    ]
    expected_text = expected_summary if mode == "summary_only" else [f"Full reasoning {n}." for n in (1, 2)]
    reasoning_events = [event for event in captured if isinstance(event, Reasoning)]
    assert [event.text for event in reasoning_events] == expected_text
    assert [event.summary for event in reasoning_events] == expected_summary
    records = [record for record in read_session_journal(tmp_path, "session") if record["type"] == "reasoning"]
    assert [record["text"] for record in records] == expected_text
    assert [record.get("summary") for record in records] == expected_summary
