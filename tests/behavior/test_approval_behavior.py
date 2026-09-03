from __future__ import annotations

import json
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import Agent, ModelSettings
from agents.testing import ModelCall, ModelStep, ScriptedModel, assistant_message

from investorch.agents import TokenUsage, create_permission_agent, review_permission
from investorch.application import ApprovalCoordinator, ApprovalResolvedEvent
from investorch.journal import SessionJournal, read_session_journal
from investorch.runtime import ApprovalRequest
from tests.support.config import make_test_config


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_manual_approval_persists_user_decision_and_attribution(tmp_path: Path, approved: bool) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    resolved: list[ApprovalResolvedEvent] = []

    async def manual_handler(_request: ApprovalRequest, _reason: str | None) -> bool:
        return approved

    async def resolved_handler(event: ApprovalResolvedEvent) -> None:
        resolved.append(event)

    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=Agent(name="Unused Permission Agent", instructions="Unused in manual mode."),
        journal=journal,
        manual_handler=manual_handler,
        resolved_handler=resolved_handler,
    )
    request = ApprovalRequest(
        approval_id="approval-a",
        run_id="run-a",
        session_id="session-a",
        user_input="edit the file",
        permission_mode="manual",
        tool_name="edit",
        arguments='{"path":"note.md"}',
    )

    outcome = await coordinator.handle(request)

    records = read_session_journal(config.session_journal_dir, "session-a")
    assert outcome.approved is approved
    assert records[0]["type"] == "approval"
    assert records[0]["approved"] is approved
    assert records[0]["source"] == "user"
    assert records[0]["approval_id"] == request.approval_id
    assert records[0]["run_id"] == request.run_id
    assert resolved[0].request == request
    assert resolved[0].approved is approved
    assert resolved[0].source == "user"
    assert resolved[0].journal_seq == records[0]["seq"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_message", "arguments"),
    [("too long", "{}"), ("ok", "arguments too long")],
)
async def test_oversized_auto_review_input_escalates_without_model_usage(
    tmp_path: Path,
    user_message: str,
    arguments: str,
) -> None:
    config = make_test_config(
        tmp_path,
        {
            "permission": {
                "max_user_instruction_chars": 3,
                "max_compacted_instruction_chars": 3,
                "max_tool_arguments_chars": 3,
                "max_reason_chars": 32,
            }
        },
    )

    result = await review_permission(
        Agent(name="Unreachable Permission Agent", instructions="Must not run for oversized inputs."),
        config,
        user_message,
        "edit",
        arguments,
    )

    assert result.review.decision == "ask"
    assert len(result.review.reason) <= 32
    assert result.usage == TokenUsage()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_instructions", "argument_overrides", "required_policy", "expected_decision"),
    [
        pytest.param(
            (
                "Record a current BUY in Portfolio portfolio-a of 10 shares of 600519 at CNY 1500. "
                "Commission is 5, tax is 0, and other fee is 0."
            ),
            {},
            "stable verifiable facts",
            "approve",
            id="stable-market-mapping",
        ),
        pytest.param(
            (
                "Record a current BUY in Portfolio portfolio-a of 10 shares of 600519. "
                "Commission is 5, tax is 0, and other fee is 0."
            ),
            {},
            "execution price",
            "ask",
            id="invented-execution-price",
        ),
        pytest.param(
            (
                "Record a current BUY in Portfolio portfolio-a of 10 shares of 600519 at CNY 1500. "
                "Tax is 0 and other fee is 0."
            ),
            {"commission": "0"},
            "zero is a material fee value",
            "ask",
            id="silent-zero-commission",
        ),
        pytest.param(
            (
                "Record a current BUY in Portfolio portfolio-a of 10 shares of 600519 at CNY 1500. "
                "Commission is 0.1% of gross consideration; tax and other fee are 0."
            ),
            {"commission": "15"},
            "deterministic derivation",
            "approve",
            id="deterministic-commission",
        ),
    ],
)
async def test_permission_review_receives_portfolio_grounding_policy_and_evidence(
    tmp_path: Path,
    user_instructions: str,
    argument_overrides: dict[str, object],
    required_policy: str,
    expected_decision: str,
) -> None:
    arguments = {
        "portfolio_id": "portfolio-a",
        "code": "600519",
        "market": "XSHG",
        "side": "BUY",
        "quantity": "10",
        "price": "1500",
        "commission": "5",
        "tax": "0",
        "other_fee": "0",
        "effective_at": None,
        **argument_overrides,
    }
    raw_arguments = json.dumps(arguments)

    def review_scenario(call: ModelCall) -> tuple[object, ...]:
        assert call.system_instructions is not None
        assert required_policy in call.system_instructions
        review_input = unescape(str(call.input))
        assert user_instructions in review_input
        assert raw_arguments in review_input
        return (
            assistant_message(
                json.dumps({"decision": expected_decision, "reason": "Grounding policy applied to supplied evidence."})
            ),
        )

    model = ScriptedModel((ModelStep.respond(review_scenario),))
    permission_agent = create_permission_agent(model, ModelSettings())  # type: ignore[arg-type]

    result = await review_permission(
        permission_agent,
        make_test_config(tmp_path),
        user_instructions,
        "record_portfolio_trade",
        raw_arguments,
    )

    assert result.review.decision == expected_decision
    model.assert_complete()
