import json
from zoneinfo import ZoneInfo

import pytest
from agents import Agent, ModelSettings
from agents.testing import ModelStep, ScriptedModel, assistant_message

from investorch.agents.skill_review import create_skill_review_agent
from investorch.application.approval import ApprovalCoordinator
from investorch.application.skills import SkillOperations
from investorch.journal import SessionJournal
from investorch.runtime import ApprovalRequest
from tests.behavior.test_skill_storage_behavior import candidate
from tests.support.config import make_test_config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "manual_called", "approved"), [("pass", True, True), ("warn", True, True), ("block", False, False)]
)
async def test_install_runs_fresh_review_before_manual_approval(tmp_path, decision, manual_called, approved):
    config = make_test_config(tmp_path)
    candidate(config.workspace_dir / ".skill-staging")
    model = ScriptedModel(
        (
            ModelStep(
                output=(
                    assistant_message(json.dumps({"decision": decision, "reason": "Candidate behavior assessed."})),
                )
            ),
        )
    )
    skills = SkillOperations(config=config, review_agent=create_skill_review_agent(model, ModelSettings()))
    observed = []

    async def manual(request, reason):
        observed.append(reason)
        return True

    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=Agent(name="Unused"),
        journal=SessionJournal(config.session_journal_dir, ZoneInfo("UTC")),
        manual_handler=manual,
        skills=skills,
    )
    arguments = dict(candidate_path=".skill-staging/example", source_type="created", origin=None, replace=False)
    request = ApprovalRequest(
        approval_id="a",
        run_id="r",
        session_id="s",
        user_input="install",
        permission_mode="manual",
        tool_name="install_skill",
        arguments=json.dumps(arguments),
    )
    outcome = await coordinator.handle(request)
    assert outcome.approved is approved
    assert bool(observed) is manual_called
    if approved:
        assert skills.install(run_id="r", **arguments)["restart_required"]
    else:
        with pytest.raises(ValueError, match="approval"):
            skills.install(run_id="r", **arguments)
    model.assert_complete()


@pytest.mark.asyncio
async def test_warn_cannot_be_auto_approved(tmp_path):
    config = make_test_config(tmp_path)
    candidate(config.workspace_dir / ".skill-staging")
    model = ScriptedModel((ModelStep(output=(assistant_message('{"decision":"warn","reason":"Uploads data."}'),)),))
    skills = SkillOperations(config=config, review_agent=create_skill_review_agent(model, ModelSettings()))
    observed = []

    async def manual(request, reason):
        observed.append(reason)
        return False

    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=Agent(name="Never called"),
        journal=SessionJournal(config.session_journal_dir, ZoneInfo("UTC")),
        manual_handler=manual,
        skills=skills,
    )
    request = ApprovalRequest(
        approval_id="a",
        run_id="r",
        session_id="s",
        user_input="install",
        permission_mode="auto",
        tool_name="install_skill",
        arguments='{"candidate_path":".skill-staging/example","source_type":"created"}',
    )
    assert not (await coordinator.handle(request)).approved
    assert observed == ["Uploads data."]
    model.assert_complete()
