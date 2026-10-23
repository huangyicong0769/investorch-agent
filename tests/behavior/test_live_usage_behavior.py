from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from agents import ModelSettings, Usage
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.application import ApplicationCallbacks, open_application_host
from investorch.presentation import serialize_session_presentation_state
from investorch.storage import set_session_title
from tests.support.config import make_test_config
from tests.support.model_connections import install_host_models
from tests.support.runtime import run_options


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "invalid_tool"])
async def test_usage_is_live_and_survives_later_failure_without_double_counting(
    tmp_path: Path, monkeypatch, outcome: str
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def second(_):
        entered.set()
        await release.wait()
        if outcome == "failed":
            raise RuntimeError("second request failed")
        output = (
            function_call("write_todos", {"todos": [{"content": "different", "status": "pending"}]}, call_id="call-1")
            if outcome == "invalid_tool"
            else assistant_message("done")
        )
        return ModelStep(
            output=(output,), usage=Usage(requests=1, input_tokens=3000, output_tokens=300, total_tokens=3300)
        )

    model = ScriptedModel(
        (
            ModelStep(
                output=(function_call("write_todos", {"todos": []}, call_id="call-1"),),
                usage=Usage(requests=1, input_tokens=2000, output_tokens=200, total_tokens=2200),
            ),
            ModelStep.respond(second),
        )
    )
    config = make_test_config(tmp_path, {"compaction": {"auto_enabled": False}})
    install_host_models(monkeypatch, lambda *_: (model, ModelSettings()))
    snapshots = []

    async def approved(*_):
        return True

    def changed(session_id, state):
        snapshots.append((session_id, serialize_session_presentation_state(state)))

    async with open_application_host(
        config,
        manual_approval_handler=approved,
        callbacks=ApplicationCallbacks(handle_presentation_state=changed),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        session_id = await host.sessions.create()
        other_id = await host.sessions.create()
        set_session_title(config.sessions_db, session_id, "Already titled")
        run = host.runtime.start_run(session_id, "Use write_todos and continue", run_options())
        await asyncio.wait_for(entered.wait(), 5)
        midway = host.presentation_state.get(session_id)
        assert midway.main_usage.total_tokens == 2200
        assert midway.auxiliary_usage.total_tokens == 0
        assert 2200 <= midway.main_context_tokens < 3000
        assert midway.context_kind == "estimated"
        assert any(payload["main_context_tokens"] == 2200 for _, payload in snapshots)
        assert not run.task.done()
        if outcome == "cancelled":
            host.runtime.cancel_run(session_id)
        else:
            release.set()
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await run.task
        elif outcome == "failed":
            with pytest.raises(RuntimeError, match="second request failed"):
                await run.task
        elif outcome == "invalid_tool":
            with pytest.raises(Exception, match=r"different|conflict|reus"):
                await run.task
        else:
            await run.task
        final = host.presentation_state.get(session_id)
        expected = 5500 if outcome in {"completed", "invalid_tool"} else 2200
        assert final.main_usage.total_tokens == expected
        assert final.usage.total_tokens == expected
        assert final.auxiliary_usage.total_tokens == 0
        if outcome in {"completed", "invalid_tool"}:
            assert final.main_context_tokens == 3300
            assert final.context_kind == "measured"
        else:
            assert final.main_context_tokens == midway.main_context_tokens
            assert final.context_kind == "estimated"
        assert host.presentation_state.get(other_id).usage.total_tokens == 0
        versions = [payload["revision"] for sid, payload in snapshots if sid == session_id]
        assert versions == sorted(set(versions))
        assert snapshots[-1][1]["main_usage"]["total_tokens"] == expected
        assert snapshots[-1][1]["context_model"] == host.session_models.view(session_id).effective.name
