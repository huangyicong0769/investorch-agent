from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import Agent, SQLiteSession, function_tool
from agents.testing import ScriptedModel, assistant_message, function_call

from investorch.agents import AgentLoop, PermissionReview
from investorch.application import ApprovalCoordinator, PortfolioOperations, ReviewContext
from investorch.context import ExecutionState
from investorch.journal import SessionJournal
from investorch.runtime import ApprovalRequest
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from tests.support.config import make_test_config


@pytest.mark.parametrize(
    ("decision", "approved"), [("approve", True), ("reject", False), ("ask", True), ("ask", False)]
)
@pytest.mark.parametrize("call_count", [1, 2])
async def test_main_receives_review_reason_and_final_decision_after_approval(
    tmp_path: Path, decision: str, approved: bool, call_count: int
) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "Already titled")
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    head = await journal.record_user_message("session", "Implement and evaluate all submitted candidates.")
    reason = "Confirm whether this changes data access or hardcodes a submitted factor."
    credential = config.secret_values()[0]
    review_reasons = [reason + f" Candidate {index}. Credential: " + credential for index in range(call_count)]
    reviewer = ScriptedModel(
        tuple((assistant_message(json.dumps({"decision": decision, "reason": value})),) for value in review_reasons)
    )
    manual_reasons = []

    async def manual(_request, received_reason):
        manual_reasons.append(received_reason)
        return approved

    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=Agent(name="Permission Agent", model=reviewer, output_type=PermissionReview),
        journal=journal,
        manual_handler=manual,
    )
    executed = []

    @function_tool(needs_approval=True)
    async def edit_candidate() -> str:
        executed.append(True)
        return "Candidate updated."

    @function_tool
    async def inspect_candidate() -> str:
        return "Candidate inspected."

    model = ScriptedModel(
        (
            tuple(function_call("edit_candidate", {}, call_id=f"edit-call-{index}") for index in range(call_count)),
            (function_call("inspect_candidate", {}, call_id="inspect-call"),),
            (assistant_message("done"),),
        )
    )
    main = Agent(name="Main", model=model, tools=[edit_candidate, inspect_candidate])
    unused = Agent(name="Unused", model=ScriptedModel())
    loop = AgentLoop(main, unused, unused, config, PortfolioOperations(config=config), lambda _: model)

    approval_count = 0

    async def approval(head_seq, name, arguments):
        nonlocal approval_count
        approval_count += 1
        return await coordinator.handle(
            ApprovalRequest(
                approval_id=f"approval-{approval_count}",
                run_id="run",
                session_id="session",
                user_input="Implement and evaluate all submitted candidates.",
                permission_mode="review",
                tool_name=name,
                arguments=arguments,
                instruction_head_seq=head_seq,
            )
        )

    async def output(_):
        pass

    session = SQLiteSession("session", config.sessions_db)
    try:
        result = await loop.run(
            "Implement and evaluate all submitted candidates.",
            session,
            ExecutionState(),
            run_id="run",
            session_id="session",
            model_config=config.agent_model("main"),
            approval_handler=approval,
            output_handler=output,
            run_control=RunControl("session", "run", lambda: None),
            instruction_head_seq=head,
        )
        assert result.output == "done"
        assert executed == ([True] * call_count if approved else [])
        assert manual_reasons == (review_reasons if decision == "ask" else [])
        assert result.auxiliary_usage.requests == call_count
        resumed = json.dumps(model.calls[1].input, ensure_ascii=False)
        assert reason in resumed
        assert credential not in resumed
        feedbacks = [
            item["content"] if approved else item["output"]
            for item in model.calls[1].input
            if (
                item.get("role") == "developer"
                if approved
                else str(item.get("call_id")).startswith("edit-call-") and "output" in item
            )
        ]
        assert len(feedbacks) == call_count
        for index, feedback in enumerate(feedbacks):
            facts = json.loads(feedback.split("\n", 1)[1])
            assert facts["tool_name"] == "edit_candidate"
            assert facts["tool_call_id"] == f"edit-call-{index}"
            assert facts["approved"] is approved
            assert facts["source"] == ("user" if decision == "ask" else "permission")
            assert facts["review_decision"] == decision
            assert facts["review_reason"] == review_reasons[index].replace(credential, "<redacted>")
        # Successful review feedback is consumed once; rejection stays with its SDK tool output.
        if approved:
            assert reason not in json.dumps(model.calls[2].input)
            assert reason not in json.dumps(await session.get_items())
        else:
            assert reason in json.dumps(await session.get_items())
        assert not any(item.get("role") == "user" and reason in str(item) for item in model.calls[1].input)
        next_head = await journal.record_user_message("session", "Continue evaluation.")
        prepared = await ReviewContext(config=config).prepare("session", next_head)
        assert prepared.instruction_count == 2
        assert reason not in prepared.text
        reviewer.assert_complete()
        model.assert_complete()
    finally:
        session.close()
