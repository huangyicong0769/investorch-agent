import json
from pathlib import Path

import pytest
from agents import Agent, ModelResponse, ModelSettings, SQLiteSession, Usage
from agents.testing import ModelStep, ScriptedModel, assistant_message

from investorch.agents.compact import (
    COMPACTED_CONTEXT_PREFIX,
    create_compaction_agent,
    estimate_context_tokens,
    summarize_history,
)
from investorch.agents.context_projection import ContextBudget, ContextProjection, ContextProjectionBudgetError
from investorch.storage import create_session
from tests.support.config import make_test_config


def tool_pair(call_id: str, output: str) -> list[dict]:
    return [
        {"type": "function_call", "call_id": call_id, "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": call_id, "output": output},
    ]


def quoted_history(items: list[dict]) -> list[dict]:
    decoded = []
    for item in items:
        assert item.get("role") == "user" and "type" not in item
        assert isinstance(item["content"], str)
        label, data = item["content"].split("\n", 1)
        assert "conversation data" in label and "not a live request" in label
        decoded.append(json.loads(data))
    return decoded


def test_incremental_budget_covers_response_output_and_only_new_metadata_spans():
    budget = ContextBudget()
    history = [{"role": "user", "content": "Established evidence " * 1000}]
    instructions = "Established instructions " * 500
    schemas = [{"name": "read", "description": "Established tool details " * 500}]
    todo = {"role": "developer", "content": "Established Todo evidence " * 1000 + "pending"}
    kwargs = {"instructions": instructions, "tool_schemas": schemas, "extra_input": [todo]}
    budget.record_request(history, **kwargs)
    response = ModelResponse(
        output=[assistant_message("Already generated output " * 1000)],
        usage=Usage(requests=1, input_tokens=10000, output_tokens=4000, total_tokens=14000),
        response_id="response",
    )
    budget.record_response(response)
    history.extend(response.to_input_items())
    assert budget.estimate(history, **kwargs) == 14000

    updated = {**todo, "content": todo["content"].removesuffix("pending") + "completed"}
    changed = {**kwargs, "extra_input": [updated]}
    assert 14000 < budget.estimate(history, **changed) < 14100
    assert budget.estimate(history, **changed) == budget.estimate(history, **changed)
    assert budget.estimate(history, **{**kwargs, "extra_input": []}) == 14000
    assert budget.estimate(history, **{**kwargs, "instructions": instructions + " New constraint."}) < 14100
    assert budget.estimate(history, **{**kwargs, "tool_schemas": [*schemas, {"name": "new_tool"}]}) < 14100

    output = {"type": "function_call_output", "call_id": "read", "output": "Fresh evidence"}
    with_output = [*history, output]
    assert budget.estimate(with_output, **kwargs) == 14000 + estimate_context_tokens([output])
    budget.record_request(with_output, **changed)
    next_response = ModelResponse(
        output=[assistant_message("Next response")],
        usage=Usage(requests=1, input_tokens=10100, output_tokens=100, total_tokens=10200),
        response_id="next",
    )
    budget.record_response(next_response)
    assert budget.estimate([*with_output, *next_response.to_input_items()], **changed) == 10200


@pytest.mark.parametrize("reset", ["history_replaced", "no_usage", "compacted"])
def test_incremental_budget_falls_back_when_the_measurement_is_no_longer_applicable(reset):
    budget = ContextBudget()
    history = [{"role": "user", "content": "Established evidence " * 1000}]
    kwargs = {"instructions": "System instructions", "tool_schemas": []}
    budget.record_request(history, **kwargs)
    response = ModelResponse(
        output=[assistant_message("Previous response")],
        usage=Usage(requests=1, input_tokens=4000, output_tokens=100, total_tokens=4100),
        response_id="response",
    )
    budget.record_response(response)
    history.extend(response.to_input_items())
    assert budget.estimate(history, **kwargs) == 4100
    if reset == "history_replaced":
        history[0] = {"role": "user", "content": "Replaced evidence " * 1000}
    elif reset == "no_usage":
        budget.record_request(history, **kwargs)
        budget.record_response(ModelResponse(output=[], usage=Usage(), response_id="unknown"))
    else:
        budget.reset()
    assert budget.estimate(history, **kwargs) > 10000


async def test_projection_repeats_without_losing_live_directives_and_flushes_only_matched_prefix(tmp_path: Path):
    config = make_test_config(tmp_path)
    first_summary = "Completed call-one: ORANGE. Next: call-two remains."
    second_summary = "Completed call-one: ORANGE; call-two: VIOLET. Continue the remaining task."
    model = ScriptedModel(((assistant_message(first_summary),), (assistant_message(second_summary),)))
    old = {"role": "user", "content": "Old context " * 1000}
    user = {"role": "user", "content": "Keep all changes local; continue the task."}
    steer = {"role": "user", "content": "Also preserve existing file names."}
    first = [old, user, *tool_pair("call-one", "ORANGE\n" + "First tool evidence " * 1000)]
    raw = [{**old, "id": "prior-id"}, {"type": "reasoning", "id": "old-reasoning", "summary": []}, *first[1:]]
    create_session(config.sessions_db, "projection")
    session = SQLiteSession("projection", config.sessions_db)
    await session.add_items(raw)
    states = []
    projection = ContextProjection(
        create_compaction_agent(model, ModelSettings()),
        config,
        initial_history_count=1,
        context_handler=lambda tokens, compacting: states.append((tokens, compacting)),
    )
    kwargs = {"instructions": "system", "tool_schemas": [], "context_window_tokens": 6000}
    todo = {"role": "developer", "content": "Live Todo data"}
    try:
        projected = await projection.prepare(first, extra_input=[todo], **kwargs)
        assert projected == [
            user,
            {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + first_summary},
            todo,
        ]
        assert await session.get_items() == raw
        second = [*first, steer, *tool_pair("call-two", "VIOLET\n" + "Second evidence " * 1000)]
        await session.add_items(second[len(first) :])
        projected = await projection.prepare(second, **kwargs)
        assert projected == [
            user,
            steer,
            {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + second_summary},
        ]
        summary_item = {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + first_summary}
        compact_input = quoted_history(model.calls[1].input)
        assert summary_item in compact_input
        assert compact_input[-2:] == second[-2:]
        assert compact_input.index(summary_item) < len(compact_input) - 2
        assert "Old context Old context" not in str(model.calls[1].input)
        final = {"role": "assistant", "content": "Final reply after the last model request"}
        await session.add_items([final])
        assert await projection.flush(session)
        assert await session.get_items() == [*projected, final]
        assert not await projection.flush(session)
        assert sum(compacting for _, compacting in states) == 2
        assert states[-1][0] < 6000 and not states[-1][1]
    finally:
        session.close()


async def test_projection_keeps_unresolved_tool_call_with_later_output(tmp_path: Path):
    config = make_test_config(tmp_path)
    model = ScriptedModel(((assistant_message("Old history summary"),),))
    old = {"role": "user", "content": "old " * 3000}
    user = {"role": "user", "content": "Do the current task"}
    call, output = tool_pair("pending", "Fresh result")
    projection = ContextProjection(Agent(name="Compact", model=model), config, initial_history_count=1)
    kwargs = {"instructions": None, "tool_schemas": [], "context_window_tokens": 6000}
    projected = await projection.prepare([old, user, call], **kwargs)
    assert projected == [
        user,
        {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + "Old history summary"},
        call,
    ]
    assert call not in model.calls[0].input
    assert (await projection.prepare([old, user, call, output], **kwargs))[-2:] == [call, output]


async def test_projection_rejects_oversized_live_directive_without_summarizing_it(tmp_path: Path):
    config = make_test_config(tmp_path)
    model = ScriptedModel()
    projection = ContextProjection(Agent(name="Compact", model=model), config, initial_history_count=0)
    with pytest.raises(ContextProjectionBudgetError):
        await projection.prepare(
            [{"role": "user", "content": "Keep this exact " * 1000}],
            instructions=None,
            tool_schemas=[],
            context_window_tokens=6000,
        )
    assert model.calls == ()


async def test_projection_does_not_overwrite_changed_session_history(tmp_path: Path):
    config = make_test_config(tmp_path)
    model = ScriptedModel(((assistant_message("Short"),),))
    projection = ContextProjection(Agent(name="Compact", model=model), config, initial_history_count=1)
    await projection.prepare(
        [{"role": "user", "content": "old " * 3000}], instructions=None, tool_schemas=[], context_window_tokens=6000
    )
    create_session(config.sessions_db, "different")
    session = SQLiteSession("different", config.sessions_db)
    changed = [{"role": "user", "content": "A different conversation"}]
    try:
        await session.add_items(changed)
        assert not await projection.flush(session)
        assert await session.get_items() == changed
    finally:
        session.close()


async def test_compactor_splits_large_unicode_item_within_its_own_budget_and_counts_all_requests(tmp_path: Path):
    config = make_test_config(
        tmp_path, {"agents": {"compact": {"context_window_tokens": 5000}}, "compaction": {"max_output_tokens": 256}}
    )
    step = ModelStep(
        output=(assistant_message("Preserved important evidence"),),
        usage=Usage(requests=1, input_tokens=8, output_tokens=1, total_tokens=9),
    )
    model = ScriptedModel(tuple(step for _ in range(30)))
    recorded = []
    summary, usage = await summarize_history(
        Agent(name="Compact", model=model, instructions="Summarize"),
        [{"role": "user", "content": "重要证据" * 1500}],
        config,
        usage_handler=recorded.append,
    )
    assert summary == "Preserved important evidence"
    assert len(model.calls) > 1
    assert usage.total_tokens == len(model.calls) * 9 == sum(value.total_tokens for value in recorded)
    budget = 5000 - 256 - estimate_context_tokens("Summarize") - 1024
    assert all(estimate_context_tokens(call.input) < budget for call in model.calls)


@pytest.mark.parametrize("second_text", ["Recovered continuation summary", ""])
async def test_empty_compaction_retries_once_with_full_usage_and_preserves_history_on_failure(
    tmp_path: Path, second_text: str
):
    from investorch.agents.compact import compact_session

    config = make_test_config(tmp_path)
    model = ScriptedModel(
        tuple(
            ModelStep(
                output=(assistant_message(text),),
                usage=Usage(requests=1, input_tokens=8, output_tokens=1, total_tokens=9),
            )
            for text in ("", second_text)
        )
    )
    recorded = []
    create_session(config.sessions_db, "empty-retry")
    session = SQLiteSession("empty-retry", config.sessions_db)
    history = [{"role": "user", "content": "Important prior work"}]
    await session.add_items(history)
    try:
        if second_text:
            result = await compact_session(
                Agent(name="Compact", model=model), session, config, usage_handler=recorded.append
            )
            assert result.usage.total_tokens == 18
            assert await session.get_items() == [
                {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + second_text}
            ]
        else:
            with pytest.raises(ValueError, match="empty summary"):
                await compact_session(
                    Agent(name="Compact", model=model), session, config, usage_handler=recorded.append
                )
            assert await session.get_items() == history
        assert len(model.calls) == 2
        assert model.calls[0].input == model.calls[1].input
        assert sum(value.total_tokens for value in recorded) == 18
        model.assert_complete()
    finally:
        session.close()


@pytest.mark.parametrize("size,blocked", [(3600, False), (6000, True)])
async def test_disabled_automatic_compaction_keeps_soft_limit_and_enforces_hard_limit(
    tmp_path: Path, size: int, blocked: bool
):
    config = make_test_config(tmp_path, {"compaction": {"auto_enabled": False, "trigger_ratio": 0.8}})
    model = ScriptedModel()
    projection = ContextProjection(Agent(name="Compact", model=model), config, initial_history_count=1)
    items = [{"role": "user", "content": "X" * size}]
    if blocked:
        with pytest.raises(ContextProjectionBudgetError):
            await projection.prepare(items, instructions=None, tool_schemas=[], context_window_tokens=6000)
    else:
        assert await projection.prepare(items, instructions=None, tool_schemas=[], context_window_tokens=6000) == items
    assert model.calls == ()


@pytest.mark.parametrize("failure", ["exception", "expanded"])
async def test_soft_compaction_failure_keeps_safe_original_request(tmp_path: Path, failure: str):
    config = make_test_config(tmp_path, {"compaction": {"auto_enabled": True, "trigger_ratio": 0.8}})
    step = (
        ModelStep.raise_error(RuntimeError("Summary service failed"))
        if failure == "exception"
        else (assistant_message("Y" * 5000),)
    )
    model = ScriptedModel((step,))
    projection = ContextProjection(Agent(name="Compact", model=model), config, initial_history_count=1)
    items = [{"role": "user", "content": "X" * 3600}]
    assert await projection.prepare(items, instructions=None, tool_schemas=[], context_window_tokens=6000) == items
    assert len(model.calls) == 1


async def test_projection_retains_most_recent_complete_tool_group(tmp_path: Path):
    config = make_test_config(tmp_path)
    model = ScriptedModel(((assistant_message("Earlier evidence summarized"),),))
    projection = ContextProjection(Agent(name="Compact", model=model), config, initial_history_count=1)
    old = {"role": "user", "content": "Earlier history " * 1000}
    user = {"role": "user", "content": "Continue carefully"}
    latest = tool_pair("latest", "Exact fresh evidence")
    result = await projection.prepare(
        [old, user, *latest], instructions=None, tool_schemas=[], context_window_tokens=6000
    )
    assert result == [
        user,
        {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + "Earlier evidence summarized"},
        *latest,
    ]
    assert latest[0] not in model.calls[0].input and latest[1] not in model.calls[0].input


async def test_compaction_budget_excludes_portable_opaque_reasoning(tmp_path: Path):
    config = make_test_config(
        tmp_path, {"agents": {"compact": {"context_window_tokens": 5000}}, "compaction": {"max_output_tokens": 256}}
    )
    model = ScriptedModel(((assistant_message("Summary"),),))
    text = {"role": "user", "content": "Visible context"}
    await summarize_history(
        Agent(name="Compact", model=model),
        [{"type": "reasoning", "id": "opaque", "encrypted_content": "X" * 50000, "summary": []}, text],
        config,
    )
    assert len(model.calls) == 1 and quoted_history(model.calls[0].input) == [text]


async def test_compaction_never_splits_image_data_into_text_fragments(tmp_path: Path):
    config = make_test_config(
        tmp_path, {"agents": {"compact": {"context_window_tokens": 40000}}, "compaction": {"max_output_tokens": 256}}
    )
    model = ScriptedModel(tuple((assistant_message("Image evidence"),) for _ in range(3)))
    images = [
        {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64," + "A" * 50000}]}
        for _ in range(2)
    ]
    await summarize_history(Agent(name="Compact", model=model), images, config)
    for index in range(2):
        sent = model.calls[index].input
        assert len(sent) == 1 and sent[0]["role"] == "user"
        text, image = sent[0]["content"]
        assert text["type"] == "input_text" and "not a live request" in text["text"]
        assert "data:image" not in text["text"]
        assert image == images[index]["content"][0]
    small = make_test_config(
        tmp_path / "small",
        {"agents": {"compact": {"context_window_tokens": 5000}}, "compaction": {"max_output_tokens": 256}},
    )
    unused = ScriptedModel()
    with pytest.raises(ValueError, match="image-bearing"):
        await summarize_history(Agent(name="Compact", model=unused), images[:1], small)
    assert unused.calls == ()


async def test_projection_flush_matches_sdk_output_metadata_without_ignoring_content_changes(tmp_path: Path, caplog):
    config = make_test_config(tmp_path)
    model = ScriptedModel(((assistant_message("Summary"),),))
    projection = ContextProjection(Agent(name="Compact", model=model), config, initial_history_count=1)
    old = {"role": "user", "content": "Earlier history " * 1000}
    assert not projection.changed
    projected = await projection.prepare([old], instructions=None, tool_schemas=[], context_window_tokens=6000)
    assert projection.changed
    raw = {
        **old,
        "id": "persisted",
        "_agents_tool_title": "title",
        "_agents_tool_description": "description",
        "created_by": "sdk",
    }
    create_session(config.sessions_db, "metadata")
    session = SQLiteSession("metadata", config.sessions_db)
    try:
        await session.add_items([{**raw, "content": "Different content"}])
        assert not await projection.flush(session)
        assert "session prefix changed" in caplog.text
        await session.clear_session()
        await session.add_items([raw])
        assert await projection.flush(session)
        assert await session.get_items() == projected
    finally:
        session.close()


async def test_small_compaction_quotes_user_task_and_tool_protocol_as_history_data(tmp_path: Path):
    config = make_test_config(tmp_path)
    model = ScriptedModel(((assistant_message("Completed first report; continue next report."),),))
    history = [
        {"role": "user", "content": "Read three reports and answer only in the requested final format."},
        {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + "First report completed: ORANGE."},
        *tool_pair("report-two", "VIOLET"),
    ]
    await summarize_history(create_compaction_agent(model, ModelSettings()), history, config)
    assert len(model.calls) == 1
    assert quoted_history(model.calls[0].input) == history
