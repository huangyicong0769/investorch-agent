from pathlib import Path

import pytest
from agents import Agent, SQLiteSession, Usage
from agents.testing import ModelStep, ScriptedModel, assistant_message

from investorch.agents.compact import COMPACTED_CONTEXT_PREFIX, estimate_context_tokens, summarize_history
from investorch.agents.context_projection import ContextProjection, ContextProjectionBudgetError
from investorch.storage import create_session
from tests.support.config import make_test_config


def tool_pair(call_id: str, output: str) -> list[dict]:
    return [
        {"type": "function_call", "call_id": call_id, "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": call_id, "output": output},
    ]


async def test_projection_repeats_without_losing_live_directives_and_flushes_only_matched_prefix(tmp_path: Path):
    config = make_test_config(tmp_path)
    model = ScriptedModel(
        ((assistant_message("First compact summary"),), (assistant_message("Second compact summary"),))
    )
    old = {"role": "user", "content": "Old context " * 1000}
    user = {"role": "user", "content": "Keep all changes local; continue the task."}
    steer = {"role": "user", "content": "Also preserve existing file names."}
    first = [old, user, *tool_pair("call-one", "First tool evidence " * 1000)]
    raw = [{**old, "id": "prior-id"}, {"type": "reasoning", "id": "old-reasoning", "summary": []}, *first[1:]]
    create_session(config.sessions_db, "projection")
    session = SQLiteSession("projection", config.sessions_db)
    await session.add_items(raw)
    states = []
    projection = ContextProjection(
        Agent(name="Compact", model=model),
        config,
        initial_history_count=1,
        context_handler=lambda tokens, compacting: states.append((tokens, compacting)),
    )
    kwargs = {"instructions": "system", "tool_schemas": [], "context_window_tokens": 6000}
    todo = {"role": "developer", "content": "Live Todo data"}
    try:
        projected = await projection.prepare(first, extra_input=[todo], **kwargs)
        assert projected[1:] == [user, todo]
        assert await session.get_items() == raw
        second = [*first, steer, *tool_pair("call-two", "Second evidence " * 1000)]
        await session.add_items(second[len(first) :])
        projected = await projection.prepare(second, **kwargs)
        assert projected[1:] == [user, steer]
        assert projected[0]["content"] == COMPACTED_CONTEXT_PREFIX + "Second compact summary"
        assert "First compact summary" in str(model.calls[1].input)
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
    assert projected[1:] == [user, call]
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


async def test_empty_compaction_summary_still_records_reported_usage(tmp_path: Path):
    config = make_test_config(tmp_path)
    model = ScriptedModel(
        (
            ModelStep(
                output=(assistant_message(""),),
                usage=Usage(requests=1, input_tokens=8, output_tokens=1, total_tokens=9),
            ),
        )
    )
    recorded = []
    with pytest.raises(ValueError, match="empty summary"):
        await summarize_history(
            Agent(name="Compact", model=model),
            [{"role": "user", "content": "History"}],
            config,
            usage_handler=recorded.append,
        )
    assert sum(value.total_tokens for value in recorded) == 9


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
    assert result[1:] == [user, *latest]
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
    assert len(model.calls) == 1 and model.calls[0].input == [text]


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
    assert model.calls[0].input == [images[0]]
    assert model.calls[1].input == [images[1]]
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
