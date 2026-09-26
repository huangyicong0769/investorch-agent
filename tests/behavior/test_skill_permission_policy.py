from __future__ import annotations

import json
from html import unescape
from pathlib import Path

import pytest
from agents import ModelSettings
from agents.testing import ModelCall, ModelStep, ScriptedModel, assistant_message

from investorch.agents import create_permission_agent, review_permission
from tests.support.config import make_test_config


@pytest.mark.parametrize(
    ("tool_name", "arguments", "user_instructions", "expected_decision"),
    [
        ("edit", {"path": ".skill-staging/example/SKILL.md"}, "Create a candidate example Skill.", "approve"),
        ("edit", {"path": "skills/example/SKILL.md"}, "Improve my report.", "ask"),
        ("delete", {"path": "skills/example"}, "Clean temporary report files.", "ask"),
        ("exec_command", {"command": "rm skills/example/SKILL.md"}, "Clean temporary report files.", "ask"),
        (
            "edit",
            {"path": "skills/example/SKILL.md"},
            "Update the installed external example Skill description to cover quarterly reports.",
            "approve",
        ),
        (
            "edit",
            {"path": "skills/qmt-strategy/SKILL.md"},
            "Overwrite the managed built-in qmt-strategy Skill in place.",
            "reject",
        ),
        (
            "install_skill",
            {"candidate_path": ".skill-staging/example", "review_decision": "pass"},
            "Inspect it.",
            "ask",
        ),
        (
            "install_skill",
            {"candidate_path": ".skill-staging/example", "review_decision": "warn"},
            "Install the example Skill.",
            "ask",
        ),
    ],
    ids=["candidate", "installed-edit", "installed-delete", "installed-exec", "authorized", "builtin", "pass", "warn"],
)
async def test_skill_permission_review_receives_management_policy_and_authorization_evidence(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, str],
    user_instructions: str,
    expected_decision: str,
) -> None:
    raw_arguments = json.dumps(arguments)

    def classify(call: ModelCall) -> tuple[object, ...]:
        policy = call.system_instructions or ""
        for phrase in ("durable future Agent behavior", "ASK unless", "fork", "candidate", "PASS", "WARN", "BLOCK"):
            assert phrase in policy
        evidence = unescape(str(call.input))
        assert user_instructions in evidence
        assert tool_name in evidence
        assert raw_arguments in evidence
        return (
            assistant_message(json.dumps({"decision": expected_decision, "reason": "Controlled policy scenario."})),
        )

    model = ScriptedModel((ModelStep.respond(classify),))
    agent = create_permission_agent(model, ModelSettings())  # type: ignore[arg-type]
    result = await review_permission(agent, make_test_config(tmp_path), user_instructions, tool_name, raw_arguments)

    assert result.review.decision == expected_decision
    model.assert_complete()
