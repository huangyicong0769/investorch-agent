from __future__ import annotations

import json

import pytest
from agents import ModelSettings
from agents.exceptions import ModelBehaviorError
from agents.testing import ScriptedModel, assistant_message

from investorch.agents.skill_review import create_skill_review_agent, review_skill


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        json.dumps({"decision": "approve", "reason": "Not a Skill safety decision."}),
        json.dumps({"decision": "pass"}),
        json.dumps({"decision": "pass", "reason": "   "}),
        json.dumps({"decision": "pass", "reason": "x" * 2001}),
    ],
    ids=["invalid-json", "invalid-decision", "missing-reason", "empty-reason", "oversized-reason"],
)
async def test_invalid_skill_safety_output_cannot_be_used_as_a_review(response: str) -> None:
    model = ScriptedModel(((assistant_message(response),),))
    agent = create_skill_review_agent(model, ModelSettings())  # type: ignore[arg-type]

    with pytest.raises((ValueError, ModelBehaviorError)):
        await review_skill(agent, "Candidate Skill content.")

    model.assert_complete()


async def test_failed_skill_review_does_not_fabricate_a_pass() -> None:
    model = ScriptedModel((RuntimeError("controlled model failure"),))
    agent = create_skill_review_agent(model, ModelSettings())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="controlled model failure"):
        await review_skill(agent, "Candidate Skill content.")

    model.assert_complete()
