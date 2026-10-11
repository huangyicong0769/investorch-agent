import asyncio
from contextlib import asynccontextmanager

import pytest

from investorch.application.presentation_state import SessionPresentationStore
from investorch.application.sessions import SessionOperations
from investorch.application.skill_sessions import SkillSessionWorkflows
from investorch.application.skills import SkillOperations
from investorch.context import AppState
from investorch.journal import read_session_journal
from investorch.storage import list_sessions
from tests.behavior.test_skill_storage_behavior import candidate
from tests.support.runtime import make_runtime_harness


@asynccontextmanager
async def workflow_harness(tmp_path):
    runtime = make_runtime_harness(tmp_path)
    sessions = SessionOperations(
        config=runtime.config,
        runtime=runtime.runtime,
        journal=runtime.journal,
        presentation_state=SessionPresentationStore(),
    )
    skills = SkillOperations(config=runtime.config)
    state = AppState(
        config=runtime.config,
        execution=runtime.execution,
        selected_session_id="unrelated-session",
        main_reasoning_effort="none",
        permission_mode="manual",
    )
    workflows = SkillSessionWorkflows(state=state, runtime=runtime.runtime, sessions=sessions, skills=skills)
    try:
        yield runtime, skills, workflows
    finally:
        await runtime.runtime.aclose()


@pytest.mark.parametrize(
    "action,name", [("create", None), ("install", None), ("edit", "example"), ("fork", "skill-creator")]
)
async def test_skill_click_creates_visible_contextual_session_once(tmp_path, action, name):
    async with workflow_harness(tmp_path) as (runtime, skills, workflows):
        if action == "edit":
            candidate(runtime.config.workspace_dir / ".skill-staging")
            arguments = dict(run_id="setup", candidate_path=".skill-staging/example", source_type="created")
            skills.authorize_install(**arguments)
            skills.install(**arguments)
        first, duplicate = await asyncio.gather(
            workflows.start(action=action, request_id="click", name=name),
            workflows.start(action=action, request_id="click", name=name),
        )
        assert first.started is True
        assert duplicate.started is False
        assert first.session_id == duplicate.session_id
        assert first.run_id == duplicate.run_id
        await runtime.agent_loop.wait_until_started(first.session_id)
        run = runtime.agent_loop.input_for(first.session_id)
        records = read_session_journal(runtime.config.session_journal_dir, first.session_id)
        assert [(record["type"], record.get("text")) for record in records] == [("user_message", run.user_input)]
        assert run.user_input
        assert run.application_instruction
        if name:
            assert name in run.user_input
        assert len(list_sessions(runtime.config.sessions_db, include_archived=True)) == 1

        runtime.agent_loop.complete(first.session_id)
        await runtime.wait_for_run_ended(first.session_id)
        retried = await workflows.start(action=action, request_id="click", name=name)
        assert retried.session_id == first.session_id
        assert retried.started is False
        assert retried.run_id is None


@pytest.mark.parametrize(
    "action,name",
    [
        ("invalid", None),
        ("edit", None),
        ("fork", None),
        ("edit", "skill-creator"),
        ("fork", "unknown"),
        ("create", "example"),
    ],
)
async def test_invalid_skill_action_cannot_create_a_session(tmp_path, action, name):
    async with workflow_harness(tmp_path) as (runtime, _, workflows):
        with pytest.raises(ValueError):
            await workflows.start(action=action, request_id="invalid", name=name)
        assert list_sessions(runtime.config.sessions_db, include_archived=True) == []
