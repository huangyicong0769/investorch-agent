from __future__ import annotations

from pathlib import Path

import pytest
from agents import SQLiteSession

from investorch.agents import ContextBudgetError
from tests.support.config import make_test_config


async def test_context_budget_failure_is_reported_once_without_exposing_input(tmp_path: Path, monkeypatch) -> None:
    from agents import ModelSettings
    from agents.testing import ScriptedModel

    from investorch.application import ApplicationCallbacks, open_application_host
    from investorch.journal.reader import read_session_journal
    from investorch.output import AssistantMessage
    from tests.support.model_connections import install_host_models
    from tests.support.runtime import run_options

    config = make_test_config(tmp_path, {"compaction": {"auto_enabled": False}})
    model = ScriptedModel()
    install_host_models(monkeypatch, lambda *_: (model, ModelSettings()))
    outputs = []
    ended = []

    async def approved(*_):
        return True

    async def record_output(output, journal_seq):
        if isinstance(output.event, AssistantMessage):
            outputs.append((output, journal_seq))

    async def record_ended(event):
        ended.append(event)

    async with open_application_host(
        config,
        manual_approval_handler=approved,
        callbacks=ApplicationCallbacks(handle_output=record_output, handle_run_ended=record_ended),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        session_id = await host.sessions.create()
        host.session_models.update(session_id, context_window_tokens=2048)
        run = host.runtime.start_run(session_id, "private-user-input", run_options())
        with pytest.raises(ContextBudgetError):
            await run.task
        assert len(ended) == 1 and ended[0].status == "failed"
        assert model.calls == ()
        assert outputs == []
        error = ended[0].error
        assert error is not None and "larger window" in error and "Main was not executed" in error
        assert "private-" not in error
        records = read_session_journal(config.session_journal_dir, session_id)
        assert sum(record.get("error") == error for record in records) == 1


@pytest.mark.parametrize("outcome", ["too_large", "main_failed", "cancelled", "completed"])
async def test_preflight_compact_usage_stays_with_its_session_when_the_run_does_not_complete(
    tmp_path: Path, monkeypatch, outcome: str
) -> None:
    import asyncio

    from agents import ModelSettings, Usage
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    from investorch.application import ApplicationCallbacks, open_application_host
    from investorch.journal.reader import read_session_journal
    from investorch.presentation import serialize_run_ended
    from investorch.storage import set_session_title
    from tests.support.model_connections import install_host_models
    from tests.support.runtime import run_options

    config = make_test_config(tmp_path, {"compaction": {"auto_enabled": False}})
    entered_main = asyncio.Event()

    async def wait_for_stop(_):
        entered_main.set()
        await asyncio.Event().wait()

    main = ScriptedModel(
        (
            ModelStep.raise_error(RuntimeError("controlled model failure"))
            if outcome == "main_failed"
            else ModelStep.respond(wait_for_stop)
            if outcome == "cancelled"
            else (assistant_message("done"),),
        )
    )
    compact = ScriptedModel(
        (
            ModelStep(
                output=(assistant_message("X" * 150000 if outcome == "too_large" else "Prior work summarized."),),
                usage=Usage(requests=1, input_tokens=300, output_tokens=21, total_tokens=321),
            ),
        )
    )
    install_host_models(monkeypatch, lambda _config, role: (compact if role == "compact" else main, ModelSettings()))
    ended = []

    async def approved(*_):
        return True

    async def record_ended(event):
        ended.append(event)

    async with open_application_host(
        config,
        manual_approval_handler=approved,
        callbacks=ApplicationCallbacks(handle_run_ended=record_ended),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        session_id = await host.sessions.create()
        other_id = await host.sessions.create()
        set_session_title(config.sessions_db, session_id, "Already titled")
        history = SQLiteSession(session_id, config.sessions_db)
        await history.add_items([{"role": "user", "content": "X" * 300000}])
        history.close()
        # Leave room for the actual tool catalog; the seeded history still
        # exceeds this window and the oversized compact result still cannot fit.
        host.session_models.update(session_id, context_window_tokens=200000)
        run = host.runtime.start_run(session_id, "Continue", run_options())
        if outcome == "cancelled":
            await asyncio.wait_for(entered_main.wait(), 3)
            host.runtime.cancel_run(session_id)
            with pytest.raises(asyncio.CancelledError):
                await run.task
        elif outcome == "too_large":
            with pytest.raises(ContextBudgetError, match="still exceeds"):
                await run.task
            assert main.calls == ()
            records = read_session_journal(config.session_journal_dir, session_id)
            assert sum("larger window" in (record.get("error") or "") for record in records) == 1
        elif outcome == "main_failed":
            with pytest.raises(RuntimeError, match="controlled model failure"):
                await run.task
        else:
            await run.task
        assert host.presentation_state.get(session_id).usage.total_tokens == 321
        assert host.presentation_state.get(other_id).usage.total_tokens == 0
        assert len(ended) == 1
        payload = serialize_run_ended(ended[0])
        assert payload["auxiliary_usage"]["total_tokens"] == 321
        assert payload["main_usage"] is None or payload["main_usage"]["total_tokens"] == 0
        compact.assert_complete()
