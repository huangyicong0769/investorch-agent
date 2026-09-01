from __future__ import annotations

from pathlib import Path

import pytest

from qmt_agent.application.interaction import ArchivedSessionInputError, InputDisposition, submit_user_input
from qmt_agent.context import AppState, ExecutionState
from qmt_agent.runtime import FollowUpBehavior
from qmt_agent.storage import archive_session, create_session
from tests.support.runtime import RuntimeHarness, make_runtime_harness


def make_app_state(harness: RuntimeHarness, session_id: str) -> AppState:
    return AppState(
        config=harness.config,
        execution=ExecutionState(workspace_root=harness.config.workspace_dir),
        selected_session_id=session_id,
        main_reasoning_effort="none",
        permission_mode="manual",
    )


@pytest.mark.asyncio
async def test_empty_user_input_is_rejected_without_starting_a_run(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    state = make_app_state(harness, "session-a")

    with pytest.raises(ValueError):
        await submit_user_input(state=state, runtime=harness.runtime, session_id="session-a", text="  ")

    assert harness.runtime.session_snapshot("session-a").run_id is None
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_archived_session_rejects_user_input(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "session-a")
    archive_session(harness.config.sessions_db, "session-a")
    state = make_app_state(harness, "session-a")

    with pytest.raises(ArchivedSessionInputError):
        await submit_user_input(state=state, runtime=harness.runtime, session_id="session-a", text="hello")

    assert harness.runtime.session_snapshot("session-a").run_id is None
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_idle_session_input_starts_a_new_run(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    state = make_app_state(harness, "session-a")

    submission = await submit_user_input(state=state, runtime=harness.runtime, session_id="session-a", text="hello")
    await harness.agent_loop.wait_until_started("session-a")

    assert submission.disposition == "run_started"
    assert submission.session_id == "session-a"
    assert submission.run_id
    assert submission.follow_up_id is None

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("behavior", "expected_disposition"),
    [("queue", "queue_submitted"), ("steer", "steer_submitted")],
)
async def test_active_session_routes_input_using_its_run_mode(
    tmp_path: Path,
    behavior: FollowUpBehavior,
    expected_disposition: InputDisposition,
) -> None:
    harness = make_runtime_harness(tmp_path)
    state = make_app_state(harness, "session-a")
    state.follow_up_behavior = behavior
    first = await submit_user_input(state=state, runtime=harness.runtime, session_id="session-a", text="first")
    await harness.agent_loop.wait_until_started("session-a")

    follow_up = await submit_user_input(state=state, runtime=harness.runtime, session_id="session-a", text="more")

    assert follow_up.disposition == expected_disposition
    assert follow_up.run_id == first.run_id
    assert follow_up.follow_up_id

    harness.runtime.clear_queue("session-a")
    harness.runtime.cancel_run("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == "cancelled"
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_active_run_keeps_frozen_mode_when_future_default_changes(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    state = make_app_state(harness, "session-a")
    state.follow_up_behavior = "steer"
    first = await submit_user_input(state=state, runtime=harness.runtime, session_id="session-a", text="first")
    await harness.agent_loop.wait_until_started("session-a")

    state.follow_up_behavior = "queue"
    follow_up = await submit_user_input(state=state, runtime=harness.runtime, session_id="session-a", text="more")

    assert state.follow_up_behavior == "queue"
    assert harness.runtime.session_snapshot("session-a").active_follow_up_behavior == "steer"
    assert follow_up.disposition == "steer_submitted"
    assert follow_up.run_id == first.run_id

    harness.runtime.cancel_run("session-a")
    assert (await harness.wait_for_run_ended("session-a")).status == "cancelled"
    await harness.runtime.aclose()
