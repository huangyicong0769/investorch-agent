from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import Agent, ModelBehaviorError, ModelSettings, SQLiteSession, Usage, function_tool
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.usage import RequestUsage

from investorch.agents.activity import generate_activity_label
from investorch.agents.permission import create_permission_agent, review_permission
from investorch.agents.skill_review import create_skill_review_agent, review_skill
from investorch.agents.title import ensure_session_title
from investorch.agents.usage import UsageTracker, run_with_usage
from investorch.application import ApprovalCoordinator
from investorch.journal import SessionJournal
from investorch.runtime import ApprovalRequest
from investorch.storage import init_session_metadata
from tests.support.config import make_test_config


def request_usage() -> Usage:
    return Usage(requests=1, input_tokens=11, output_tokens=7, total_tokens=18)


def test_cumulative_reconciliation_is_idempotent_and_ignores_unknown_retry_context() -> None:
    updates = []
    tracker = UsageTracker(updates.append)
    cumulative = Usage()
    cumulative.add(request_usage())
    tracker.record(cumulative)
    tracker.record(cumulative)
    cumulative.add(request_usage())
    tracker.record(cumulative)
    cumulative.requests += 1
    cumulative.request_usage_entries.append(
        RequestUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            input_tokens_details=request_usage().input_tokens_details,
            output_tokens_details=request_usage().output_tokens_details,
        )
    )
    tracker.record(cumulative)
    tracker.record(cumulative)
    assert [update.total_tokens for update in updates] == [18, 18, 0]
    assert updates[-1].last_request_total_tokens is None
    assert tracker.usage.requests == 3
    assert tracker.usage.total_tokens == 36
    assert tracker.usage.last_request_total_tokens == 18


async def test_request_is_published_before_tool_and_survives_later_model_failure() -> None:
    updates = []

    @function_tool
    def observe_usage() -> str:
        """Check request accounting before a tool executes."""
        assert len(updates) == 1
        assert updates[0].total_tokens == 18
        return "observed"

    model = ScriptedModel(
        ((function_call("observe_usage", "{}", call_id="observe"),), RuntimeError("later request failed")),
        default_usage=request_usage(),
    )
    with pytest.raises(RuntimeError, match="later request failed"):
        await run_with_usage(
            Agent(name="Main", model=model, tools=[observe_usage]),
            "run",
            usage_handler=updates.append,
            run_config={"tracing_disabled": True},
        )
    assert len(updates) == 1
    model.assert_complete()


@pytest.mark.parametrize("response", ["not JSON", '{"decision":"pass","reason":""}'])
async def test_invalid_structured_output_still_records_completed_request(response: str) -> None:
    updates = []
    model = ScriptedModel(((assistant_message(response),),), default_usage=request_usage())
    with pytest.raises((ModelBehaviorError, ValueError)):
        await review_skill(create_skill_review_agent(model, ModelSettings()), "candidate", usage_handler=updates.append)
    assert len(updates) == 1
    assert updates[0].total_tokens == 18


async def test_permission_retry_accounts_both_responses(tmp_path: Path) -> None:
    updates = []
    model = ScriptedModel(
        ((assistant_message("not JSON"),), (assistant_message('{"decision":"approve","reason":"authorized"}'),)),
        default_usage=request_usage(),
    )
    result = await review_permission(
        create_permission_agent(model, ModelSettings()),
        make_test_config(tmp_path),
        "write file",
        "edit",
        "{}",
        usage_handler=updates.append,
    )
    assert result.review.decision == "approve"
    assert result.usage.requests == 2
    assert result.usage.total_tokens == 36
    assert len(updates) == 2


async def test_activity_validation_failure_retains_model_usage(tmp_path: Path) -> None:
    updates = []
    model = ScriptedModel(((assistant_message("invalid\nlabel"),),), default_usage=request_usage())
    with pytest.raises(ValueError, match="multiline"):
        await generate_activity_label(
            Agent(name="Activity", model=model),
            make_test_config(tmp_path),
            "request",
            "reasoning",
            "edit",
            "{}",
            usage_handler=updates.append,
        )
    assert len(updates) == 1
    assert updates[0].total_tokens == 18


async def test_failed_review_is_accounted_before_manual_approval(tmp_path: Path) -> None:
    updates = []
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    head = await journal.record_user_message("session", "write file")
    model = ScriptedModel(((assistant_message('{"decision":"ask","reason":""}'),),), default_usage=request_usage())

    async def manual_handler(_request, _reason):
        assert updates[0][0] == "session"
        assert updates[0][1].total_tokens == 18
        return False

    coordinator = ApprovalCoordinator(
        config=config,
        permission_agent=create_permission_agent(model, ModelSettings()),
        journal=journal,
        manual_handler=manual_handler,
        usage_handler=lambda session_id, usage: updates.append((session_id, usage)),
    )
    result = await coordinator.handle(
        ApprovalRequest(
            approval_id="approval",
            run_id="run",
            session_id="session",
            user_input="write file",
            permission_mode="review",
            tool_name="edit",
            arguments="{}",
            instruction_head_seq=head,
        )
    )
    assert result.approved is False
    assert result.usage.total_tokens == 18
    assert len(updates) == 1


async def test_title_persistence_failure_cannot_discard_completed_usage(tmp_path: Path, monkeypatch) -> None:
    updates = []
    database = tmp_path / "sessions.db"
    session = SQLiteSession("session", database)
    await session.add_items([{"role": "user", "content": "hello"}])
    init_session_metadata(database)
    model = ScriptedModel(((assistant_message("A title"),),), default_usage=request_usage())

    def fail_persistence(*_args):
        assert len(updates) == 1
        raise OSError("storage unavailable")

    monkeypatch.setattr("investorch.agents.title.set_session_title", fail_persistence)
    with pytest.raises(OSError, match="storage unavailable"):
        await ensure_session_title(Agent(name="Title", model=model), session, database, usage_handler=updates.append)
    assert updates[0].total_tokens == 18
    assert len(updates) == 1
    session.close()


async def test_candidate_review_tool_accounts_nested_model_separately_once(tmp_path: Path) -> None:
    from investorch.application.portfolios import PortfolioOperations
    from investorch.application.skills import SkillOperations
    from investorch.context import AgentContext, ExecutionState
    from investorch.tools.skills import review_skill_candidate

    config = make_test_config(tmp_path)
    candidate = config.workspace_dir / "note-summary"
    candidate.mkdir()
    (candidate / "SKILL.md").write_text(
        "---\nname: note-summary\ndescription: Summarize user notes when requested.\n---\n"
        "Read the supplied note and summarize its facts.\n"
    )
    main_updates = []
    auxiliary_updates = []
    reviewer = ScriptedModel(
        ((assistant_message('{"decision":"pass","reason":"ordinary summarization"}'),),),
        default_usage=request_usage(),
    )
    skills = SkillOperations(config=config, review_agent=create_skill_review_agent(reviewer, ModelSettings()))
    context = AgentContext(
        config=config,
        execution=ExecutionState(),
        session_id="session",
        run_id="run",
        portfolios=PortfolioOperations(config=config),
        skills=skills,
        auxiliary_usage_handler=lambda usage: auxiliary_updates.append(("session", usage)),
    )

    def observe_review(call):
        assert len(auxiliary_updates) == 1
        assert auxiliary_updates[0][0] == "session"
        assert auxiliary_updates[0][1].total_tokens == 18
        assert "ordinary summarization" in str(call.input)
        return (assistant_message("Review complete"),)

    from agents.testing import ModelStep

    main = ScriptedModel(
        (
            (function_call("review_skill_candidate", {"candidate_path": "note-summary"}, call_id="review"),),
            ModelStep.respond(observe_review),
        ),
        default_usage=request_usage(),
    )
    result = await run_with_usage(
        Agent(name="Main", model=main, tools=[review_skill_candidate]),
        "Review this candidate",
        context=context,
        usage_handler=main_updates.append,
        run_config={"tracing_disabled": True},
    )
    assert result.final_output == "Review complete"
    assert sum(usage.total_tokens for usage in main_updates) == 36
    assert sum(usage.total_tokens for _session, usage in auxiliary_updates) == 18
    assert len(auxiliary_updates) == 1
    main.assert_complete()
    reviewer.assert_complete()
