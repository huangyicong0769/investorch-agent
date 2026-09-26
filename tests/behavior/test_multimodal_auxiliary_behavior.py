from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from agents import Agent, SQLiteSession
from agents.testing import ModelStep, ScriptedModel, assistant_message

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage, compact_session
from investorch.application import PortfolioOperations
from investorch.application.activity import ActivityCoordinator
from investorch.context import ExecutionState
from investorch.images import ImageContent, UserInput, user_input_to_response_item
from investorch.journal import read_session_journal
from investorch.output import ToolCalled
from investorch.runtime import RuntimeOutput
from investorch.runtime.control import RunControl
from investorch.storage import create_session, get_session_title
from tests.support.config import make_test_config
from tests.support.runtime import make_runtime_harness, run_options

IMAGE_INPUT = UserInput("", (ImageContent("data:image/png;base64,iVBORw0KGgo="),))


@pytest.mark.asyncio
async def test_title_vision_failure_does_not_fail_main_image_run(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    session = SQLiteSession("session", config.sessions_db)
    outputs = []

    def reject_vision(call):
        assert user_input_to_response_item(IMAGE_INPUT) in call.input
        raise RuntimeError("Provider does not accept image input")

    main = Agent(name="Main", model=ScriptedModel(((assistant_message("Main completed"),),)))
    title = Agent(name="Title", model=ScriptedModel((ModelStep.respond(reject_vision),)))
    loop = AgentLoop(main, title, title, config, PortfolioOperations(config=config))

    async def approve(*_args):
        return ApprovalOutcome(approved=True, usage=TokenUsage())

    async def output(event):
        outputs.append(event)

    try:
        result = await loop.run(
            IMAGE_INPUT,
            session,
            ExecutionState(workspace_root=config.workspace_dir),
            run_id="run",
            session_id="session",
            reasoning_effort="none",
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", "run", lambda: None),
        )
        assert result.output == "Main completed"
        assert outputs[-1].text == "Main completed"
        assert get_session_title(config.sessions_db, "session") is None
        assert user_input_to_response_item(IMAGE_INPUT) in await session.get_items()
    finally:
        session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "replacement"])
async def test_failed_compaction_preserves_original_multimodal_history(tmp_path: Path, failure: str) -> None:
    config = make_test_config(tmp_path)

    class FailingReplacementSession(SQLiteSession):
        fail_next_write = False

        async def add_items(self, items):
            if self.fail_next_write:
                self.fail_next_write = False
                raise OSError("Injected storage failure")
            return await super().add_items(items)

    session = FailingReplacementSession("session", config.sessions_db)
    history = [user_input_to_response_item(IMAGE_INPUT), {"role": "assistant", "content": "An image"}]
    await session.add_items(history)
    session.fail_next_write = failure == "replacement"

    def summarize(call):
        assert call.input == history
        if failure == "provider":
            raise RuntimeError("Provider does not accept images")
        return (assistant_message("Summary"),)

    agent = Agent(name="Compact", model=ScriptedModel((ModelStep.respond(summarize),)))
    try:
        with pytest.raises((RuntimeError, OSError)):
            await compact_session(agent, session, config)
        assert await session.get_items() == history
    finally:
        session.close()


@pytest.mark.asyncio
async def test_activity_observation_keeps_image_payload_out_of_model_and_journal(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    generated = asyncio.Event()

    def label(call):
        assert IMAGE_INPUT.images[0].image_url not in str(call.input)
        assert "base64" not in str(call.input)
        return (assistant_message("Inspecting chart"),)

    async def delivered(_event):
        generated.set()

    coordinator = ActivityCoordinator(
        config=harness.config,
        activity_agent=Agent(name="Activity", model=ScriptedModel((ModelStep.respond(label),))),
        journal=harness.journal,
        runtime=harness.runtime,
        label_handler=delivered,
    )
    try:
        active = harness.runtime.start_run("session", IMAGE_INPUT, run_options())
        await harness.agent_loop.wait_until_started("session")
        event = ToolCalled(name="explore", arguments='{"operation":"read","path":"chart.png"}')
        seq = await harness.journal.record_output("session", event)
        coordinator.observe(RuntimeOutput(active.run_id, "session", event), journal_seq=seq)
        await asyncio.wait_for(generated.wait(), 2)
        records = read_session_journal(harness.config.session_journal_dir, "session")
        label_record = next(record for record in records if record["type"] == "activity_label")
        assert label_record["text"] == "Inspecting chart"
        assert "base64" not in str(label_record)
    finally:
        await coordinator.aclose()
        await harness.runtime.aclose()
