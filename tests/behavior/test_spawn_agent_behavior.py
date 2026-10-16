import asyncio

import pytest

from investorch.application.spawning import SpawnError, spawn_agent
from investorch.context import AppState
from investorch.inputs import InputOrigin
from investorch.runtime import RunCapacityExceededError
from investorch.storage import get_session, list_sessions
from tests.support.runtime import run_options
from tests.support.sessions import make_session_harness


async def test_spawn_submits_a_normal_origin_input_once_and_keeps_selection(tmp_path):
    harness = make_session_harness(tmp_path)
    parent = await harness.operations.create()
    state = AppState(config=harness.runtime.config, execution=harness.runtime.execution, selected_session_id=parent)
    parent_run = harness.runtime.runtime.start_run(parent, "parent", run_options())
    await harness.runtime.agent_loop.wait_until_started(parent)
    arguments = dict(
        state=state,
        runtime=harness.runtime.runtime,
        sessions=harness.operations,
        source_session_id=parent,
        source_run_id=parent_run.run_id,
        tool_call_id="spawn-call",
        task="Research",
    )
    try:
        receipt = await spawn_agent(**arguments)
        await harness.runtime.agent_loop.wait_until_started(receipt.session_id)
        retry = await spawn_agent(**arguments)
        assert retry.session_id == receipt.session_id and retry.started is False
        delivered = harness.runtime.agent_loop.input_for(receipt.session_id)
        assert delivered.structured_input.origin == InputOrigin("agent", parent, parent_run.run_id)
        assert delivered.application_instruction is None
        assert state.selected_session_id == parent
        assert len(list_sessions(harness.runtime.config.sessions_db)) == 2
        harness.runtime.agent_loop.complete(receipt.session_id)
        await harness.runtime.runtime.get_active_run(receipt.session_id).task
        ended_retry = await spawn_agent(**arguments)
        assert ended_retry.session_id == receipt.session_id and ended_retry.started is False
        assert harness.runtime.runtime.session_snapshot(receipt.session_id).run_id is None
        assert get_session(harness.runtime.config.sessions_db, receipt.session_id).spawned_by_session_id == parent
    finally:
        await harness.runtime.runtime.aclose()


async def test_capacity_refusal_removes_only_the_unstarted_child_and_allows_retry(tmp_path):
    harness = make_session_harness(tmp_path)
    parent = await harness.operations.create()
    state = AppState(config=harness.runtime.config, execution=harness.runtime.execution, selected_session_id=parent)
    arguments = dict(
        state=state,
        runtime=harness.runtime.runtime,
        sessions=harness.operations,
        source_session_id=parent,
        source_run_id="parent-run",
        tool_call_id="spawn-call",
        task="Research",
    )
    try:
        harness.runtime.config.update("runtime.max_concurrent_runs", 1, persist=False)
        active = harness.runtime.runtime.start_run(parent, "parent", run_options())
        await harness.runtime.agent_loop.wait_until_started(parent)
        arguments["source_run_id"] = active.run_id
        with pytest.raises(RunCapacityExceededError):
            await spawn_agent(**arguments)
        assert [item.session_id for item in list_sessions(harness.runtime.config.sessions_db)] == [parent]
        harness.runtime.config.update("runtime.max_concurrent_runs", 2, persist=False)
        receipt = await spawn_agent(**arguments)
        await harness.runtime.agent_loop.wait_until_started(receipt.session_id)
        assert receipt.started
    finally:
        await harness.runtime.runtime.aclose()


async def test_recursive_children_are_normal_runs_and_outlive_the_parent(tmp_path):
    harness = make_session_harness(tmp_path)
    parent = await harness.operations.create()
    state = AppState(config=harness.runtime.config, execution=harness.runtime.execution, selected_session_id=parent)
    common = dict(state=state, runtime=harness.runtime.runtime, sessions=harness.operations, task="Research")
    try:
        active = harness.runtime.runtime.start_run(parent, "parent", run_options())
        await harness.runtime.agent_loop.wait_until_started(parent)
        child = await spawn_agent(**common, source_session_id=parent, source_run_id=active.run_id, tool_call_id="child")
        await harness.runtime.agent_loop.wait_until_started(child.session_id)
        grandchild = await spawn_agent(
            **common, source_session_id=child.session_id, source_run_id=child.run_id, tool_call_id="grandchild"
        )
        await harness.runtime.agent_loop.wait_until_started(grandchild.session_id)
        assert len(harness.runtime.runtime.list_active_runs()) == 3
        harness.runtime.runtime.cancel_run(parent)
        with pytest.raises(asyncio.CancelledError):
            await active.task
        assert harness.runtime.runtime.is_session_active(child.session_id)
        assert harness.runtime.runtime.is_session_active(grandchild.session_id)
        record = get_session(harness.runtime.config.sessions_db, grandchild.session_id)
        assert record.spawned_by_session_id == child.session_id
        assert record.spawned_by_run_id == child.run_id
        assert record.branch_from_session_id is None
    finally:
        await harness.runtime.runtime.aclose()


@pytest.mark.parametrize("task,title", [(" ", None), ("x", " "), ("x", "t" * 201)])
async def test_invalid_spawn_arguments_do_not_create_a_child(tmp_path, task, title):
    harness = make_session_harness(tmp_path)
    parent = await harness.operations.create()
    state = AppState(config=harness.runtime.config, execution=harness.runtime.execution, selected_session_id=parent)
    with pytest.raises(SpawnError, match="spawn_invalid_task"):
        await spawn_agent(
            state=state,
            runtime=harness.runtime.runtime,
            sessions=harness.operations,
            source_session_id=parent,
            source_run_id="run",
            tool_call_id="call",
            task=task,
            title=title,
        )
    assert len(list_sessions(harness.runtime.config.sessions_db)) == 1
    await harness.runtime.runtime.aclose()


async def test_closed_runtime_refusal_does_not_leave_a_ghost_child(tmp_path):
    harness = make_session_harness(tmp_path)
    parent = await harness.operations.create()
    state = AppState(config=harness.runtime.config, execution=harness.runtime.execution, selected_session_id=parent)
    await harness.runtime.runtime.aclose()
    with pytest.raises(SpawnError, match="runtime_closed"):
        await spawn_agent(
            state=state,
            runtime=harness.runtime.runtime,
            sessions=harness.operations,
            source_session_id=parent,
            source_run_id="run",
            tool_call_id="call",
            task="Research",
        )
    assert len(list_sessions(harness.runtime.config.sessions_db)) == 1


async def test_spawn_inherits_parent_active_model_and_rejects_incomplete_connection_override(tmp_path):
    from investorch.config import ConfigError

    harness = make_session_harness(tmp_path)
    parent = await harness.operations.create()
    state = AppState(config=harness.runtime.config, execution=harness.runtime.execution, selected_session_id=parent)
    runtime = harness.runtime.runtime
    runtime.session_models.update(parent, reasoning_effort="low")
    active = runtime.start_run(parent, "parent", run_options())
    await harness.runtime.agent_loop.wait_until_started(parent)
    runtime.session_models.update(parent, reasoning_effort="high")
    arguments = dict(
        state=state,
        runtime=runtime,
        sessions=harness.operations,
        source_session_id=parent,
        source_run_id=active.run_id,
        task="Research",
    )
    try:
        with pytest.raises(ConfigError):
            await spawn_agent(**arguments, tool_call_id="invalid", model="unknown")
        assert len(list_sessions(harness.runtime.config.sessions_db)) == 1
        with pytest.raises(ConfigError):
            await spawn_agent(**arguments, tool_call_id="missing-window", name="another-model")
        assert len(list_sessions(harness.runtime.config.sessions_db)) == 1
        child = await spawn_agent(**arguments, tool_call_id="valid")
        await harness.runtime.agent_loop.wait_until_started(child.session_id)
        assert runtime.get_active_run(child.session_id).options.model == active.options.model
        assert runtime.session_models.view(child.session_id).persisted.reasoning_effort == "low"
        assert runtime.session_models.view(parent).draft.reasoning_effort == "high"
    finally:
        await runtime.aclose()
