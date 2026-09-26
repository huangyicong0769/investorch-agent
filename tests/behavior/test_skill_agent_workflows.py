from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import Agent, ModelSettings, SQLiteSession
from agents.testing import ModelCall, ModelStep, ScriptedModel, assistant_message, function_call

from investorch.agents import AgentLoop, create_activity_agent, create_agent
from investorch.agents.skill_review import create_skill_review_agent
from investorch.application import ActivityCoordinator, ApprovalCoordinator, PortfolioOperations
from investorch.application.skills import SkillOperations
from investorch.context import ExecutionState
from investorch.journal import SessionJournal
from investorch.output import ToolCalled
from investorch.runtime import ApprovalRequest, RuntimeOutput
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from tests.support.config import make_test_config
from tests.support.runtime import make_runtime_harness


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["created", "external"])
async def test_main_agent_creates_or_acquires_reviews_and_installs_a_skill(tmp_path: Path, source_type: str) -> None:
    config = make_test_config(tmp_path)
    builtin = "skill-creator" if source_type == "created" else "skill-installer"
    name = f"example-{source_type}"
    candidate = f".skill-staging/{name}"
    version = "version: 1.0.0\n" if source_type == "created" else ""
    content = (
        f"---\nname: {name}\ndescription: Summarize a user note when asked to summarize notes.\n{version}---\n"
        "Read the supplied note and produce a concise factual summary.\n"
    )
    if source_type == "external":
        supplied = config.workspace_dir / "uploads" / "SKILL.md"
        supplied.parent.mkdir()
        supplied.write_text(content, encoding="utf-8")
    review_model = ScriptedModel(
        tuple(
            (assistant_message(json.dumps({"decision": "pass", "reason": "Ordinary note summarization."})),)
            for _ in range(2)
        )
    )
    skills = SkillOperations(config=config, review_agent=create_skill_review_agent(review_model, ModelSettings()))
    catalog = skills.enabled_catalog()

    def discover(call: ModelCall):
        assert builtin in call.system_instructions
        assert name not in call.system_instructions
        return (function_call("load_skill", {"name": builtin}, call_id="load-builtin"),)

    def create_candidate(call: ModelCall):
        history = str(call.input)
        assert "function_call_output" in history
        assert builtin in history
        candidate_content = content
        if source_type == "external":
            source_output = next(
                item["output"]
                for item in call.input
                if item.get("call_id") == "source" and item.get("type") == "function_call_output"
            )
            candidate_content = ast.literal_eval(source_output)["content"]
        return (
            function_call(
                "edit",
                {"path": f"{candidate}/SKILL.md", "operation": "create", "content": candidate_content, "old_text": ""},
                call_id="candidate",
            ),
        )

    steps = [ModelStep(responder=discover)]
    if source_type == "external":
        steps.append((function_call("explore", {"operation": "read", "path": "uploads/SKILL.md"}, call_id="source"),))
    steps.extend(
        [
            ModelStep(responder=create_candidate),
            (
                function_call(
                    "review_skill_candidate",
                    {"candidate_path": candidate, "source_type": source_type},
                    call_id="review",
                ),
            ),
            (
                function_call(
                    "install_skill",
                    {
                        "candidate_path": candidate,
                        "source_type": source_type,
                        "origin": "user-upload" if source_type == "external" else None,
                        "replace": False,
                    },
                    call_id="install",
                ),
            ),
            (assistant_message("Installed. Restart InvestOrch to use the Skill."),),
        ]
    )
    model = ScriptedModel(steps)
    main = create_agent(model, ModelSettings(), config, skill_catalog=catalog)
    unused = Agent(name="Unused", model=ScriptedModel())
    approvals = []

    async def manual(request, _reason):
        approvals.append(request.tool_name)
        return True

    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=unused,
        journal=SessionJournal(config.session_journal_dir, ZoneInfo("UTC")),
        manual_handler=manual,
        skills=skills,
    )

    async def approve(head, tool, arguments):
        return await coordinator.handle(
            ApprovalRequest("approval-" + tool, "run", "session", "Install this Skill", "manual", tool, arguments, head)
        )

    outputs = []

    async def output(event):
        outputs.append(event)

    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Skill workflow")
    session = SQLiteSession("session", config.sessions_db)
    loop = AgentLoop(main, unused, unused, config, PortfolioOperations(config=config), skills=skills)
    try:
        await loop.run(
            "Create a note-summary Skill"
            if source_type == "created"
            else "Install the supplied uploads/SKILL.md Skill",
            session,
            ExecutionState(workspace_root=config.workspace_dir),
            run_id="run",
            session_id="session",
            reasoning_effort="none",
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", "run", lambda: None),
        )
        history = await session.get_items()
    finally:
        session.close()

    model.assert_complete()
    review_model.assert_complete()
    assert approvals == ["edit", "install_skill"]
    assert any(isinstance(event, ToolCalled) and event.name == "load_skill" for event in outputs)
    results = {item["call_id"]: item["output"] for item in history if item.get("type") == "function_call_output"}
    assert ast.literal_eval(results["load-builtin"])["name"] == builtin
    assert ast.literal_eval(results["review"])["decision"] == "pass"
    assert ast.literal_eval(results["install"])["restart_required"] is True
    assert name not in {entry["name"] for entry in skills.enabled_catalog()}
    restarted = SkillOperations(config=config)
    assert name in {entry["name"] for entry in restarted.enabled_catalog()}
    assert restarted.load(name)["content"] == content
    assert restarted.inspect(name)["source"]["type"] == source_type

    # The real SDK load call flows through the existing generic ToolCalled activity path.
    harness = make_runtime_harness(tmp_path / "activity")
    labeled = asyncio.Event()
    labels = []

    async def receive_label(event):
        labels.append(event)
        labeled.set()

    def label_activity(call: ModelCall):
        assert "load_skill" in str(call.input)
        return (assistant_message("Loading specialized task guidance"),)

    activity_model = ScriptedModel((ModelStep(responder=label_activity),))
    activity = ActivityCoordinator(
        config=config,
        activity_agent=create_activity_agent(activity_model, ModelSettings()),
        journal=harness.journal,
        runtime=harness.runtime,
        label_handler=receive_label,
    )
    event = next(event for event in outputs if isinstance(event, ToolCalled) and event.name == "load_skill")
    seq = await harness.journal.record_output("session", event)
    try:
        activity.observe(RuntimeOutput("run", "session", event), journal_seq=seq)
        await asyncio.wait_for(labeled.wait(), timeout=2)
        assert labels[0].target_seq == seq
        assert labels[0].session_id == "session"
        assert labels[0].text
        activity_model.assert_complete()
    finally:
        await activity.aclose()
        await harness.runtime.aclose()
