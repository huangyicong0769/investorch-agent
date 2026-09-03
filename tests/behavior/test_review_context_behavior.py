from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import Agent
from agents.testing import ScriptedModel, assistant_message

from investorch.agents import PermissionReview
from investorch.application import ApprovalCoordinator, ReviewContext, ReviewContextError
from investorch.journal import SessionJournal, read_session_journal
from investorch.output import AssistantMessage, ToolOutput
from investorch.runtime import ApprovalRequest
from tests.support.config import make_test_config


@pytest.mark.asyncio
async def test_review_context_contains_only_effective_user_instructions_in_order(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    await journal.record_user_message("session-a", "earlier requirement")
    await journal.record_output("session-a", AssistantMessage(text="assistant claim"))
    await journal.record_approval("session-a", "run-a", "approval-a", "edit", "{}", True)
    await journal.record_user_message("session-a", "later correction")
    steer_seq = await journal.record_user_steer("session-a", "run-a", "active steer")
    head_seq = await journal.record_user_steers_activated("session-a", "run-a", (steer_seq,))
    await journal.record_output("session-a", ToolOutput(output="tool claim"))
    await journal.record_user_steer("session-a", "run-a", "future steer")
    await journal.record_user_message("session-a", "future queued input")

    prepared = await ReviewContext(config=config).prepare("session-a", head_seq)

    assert prepared.instruction_count == 3
    assert prepared.text.index("earlier requirement") < prepared.text.index("later correction")
    assert prepared.text.index("later correction") < prepared.text.index("active steer")
    assert "assistant claim" not in prepared.text
    assert "tool claim" not in prepared.text
    assert "future steer" not in prepared.text
    assert "future queued input" not in prepared.text


@pytest.mark.asyncio
async def test_review_context_fails_closed_for_an_unproven_active_steer(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    await journal.record_user_message("session-a", "initial")
    steer_seq = await journal.record_user_steer("session-a", "legacy-run", "legacy steer")

    with pytest.raises(ReviewContextError, match="activation"):
        await ReviewContext(config=config).prepare("session-a", steer_seq)


@pytest.mark.asyncio
async def test_permission_review_receives_the_complete_prepared_context(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    await journal.record_user_message("session-a", "earlier instruction")
    head_seq = await journal.record_user_message("session-a", "later instruction")
    model = ScriptedModel(((assistant_message('{"decision":"approve","reason":"authorized"}'),),))
    permission_agent = Agent(
        name="Permission Agent",
        instructions="review",
        model=model,
        output_type=PermissionReview,
    )

    async def unexpected_manual(_request: ApprovalRequest, _reason: str | None) -> bool:
        raise AssertionError("complete review context should not require manual approval")

    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=permission_agent,
        journal=journal,
        manual_handler=unexpected_manual,
    )
    outcome = await coordinator.handle(
        ApprovalRequest(
            approval_id="approval-a",
            run_id="run-a",
            session_id="session-a",
            user_input="later instruction",
            permission_mode="review",
            tool_name="edit",
            arguments='{"path":"note.md"}',
            instruction_head_seq=head_seq,
        )
    )

    assert outcome.approved is True
    assert model.first_call is not None
    review_input = str(model.first_call.input)
    assert "earlier instruction" in review_input
    assert "later instruction" in review_input
    assert "edit" in review_input
    assert "note.md" in review_input
    assert read_session_journal(config.session_journal_dir, "session-a")[-1]["instruction_head_seq"] == head_seq
    model.assert_complete()


@pytest.mark.asyncio
async def test_unprovable_review_context_falls_back_to_manual_ask(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    await journal.record_user_message("session-a", "initial")
    head_seq = await journal.record_user_steer("session-a", "legacy-run", "legacy steer")
    manual_reasons: list[str | None] = []

    async def manual_handler(_request: ApprovalRequest, reason: str | None) -> bool:
        manual_reasons.append(reason)
        return False

    model = ScriptedModel()
    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=Agent(name="Unreachable", instructions="test", model=model),
        journal=journal,
        manual_handler=manual_handler,
    )
    outcome = await coordinator.handle(
        ApprovalRequest(
            approval_id="approval-a",
            run_id="run-a",
            session_id="session-a",
            user_input="legacy steer",
            permission_mode="review",
            tool_name="edit",
            arguments='{"path":"note.md"}',
            instruction_head_seq=head_seq,
        )
    )

    assert outcome.approved is False
    assert manual_reasons == ["AutoReview is unavailable; manual approval is required."]
    assert model.calls == ()
