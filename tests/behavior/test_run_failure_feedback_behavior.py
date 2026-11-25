from pathlib import Path

import httpx
import httpx2
import pytest
from agents import ModelSettings
from agents.testing import ModelStep, ScriptedModel
from openai import APIConnectionError, BadRequestError

from investorch.application import ApplicationCallbacks, open_application_host
from investorch.journal import read_session_journal
from investorch.presentation import serialize_run_ended
from investorch.web.server import create_web_app
from tests.support.config import make_test_config
from tests.support.model_connections import install_host_models
from tests.support.runtime import run_options


@pytest.mark.parametrize("failure", ["disconnect", "provider_validation", "unexpected"])
async def test_failed_run_keeps_safe_cause_in_live_event_and_reloaded_history(tmp_path: Path, monkeypatch, failure):
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}})
    request = httpx2.Request("POST", "https://provider.example.test/v1/responses")
    secret = config.secret_values()[0]
    if failure == "disconnect":
        error = APIConnectionError(request=request)
        error.__cause__ = httpx2.RemoteProtocolError("Server disconnected without sending a response.")
        expected = "Server disconnected without sending a response."
    elif failure == "provider_validation":
        expected = "The `reasoning_text` in the thinking mode must be passed back to the API."
        body = {"error": {"message": expected + " " + secret, "type": "invalid_request_error"}}
        error = BadRequestError("unsafe response " + secret, response=httpx2.Response(400, request=request), body=body)
    else:
        error = RuntimeError("private request body " + secret)
        expected = "RuntimeError"

    async def fail(_call):
        raise error
        yield  # Make this a provider stream without making any network request.

    model = ScriptedModel((ModelStep.stream(fail),))
    install_host_models(monkeypatch, lambda _config, _role: (model, ModelSettings()))
    events = []

    async def capture(event):
        events.append(serialize_run_ended(event))

    async def approve(_request, _reason):
        return True

    async with open_application_host(
        config,
        manual_approval_handler=approve,
        callbacks=ApplicationCallbacks(handle_run_ended=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        session_id = await host.sessions.create()
        run = host.runtime.start_run(session_id, "Synthetic failure check", run_options())
        with pytest.raises(type(error)):
            await run.task
        assert events[-1]["status"] == "failed"
        feedback = events[-1].get("error")
        assert feedback and expected in feedback
        assert secret not in feedback
        assert "private request body" not in feedback
        records = read_session_journal(config.session_journal_dir, session_id)
        ended = next(record for record in records if record["type"] == "run_ended")
        assert ended["error"] == feedback
        app = create_web_app(config)
        app.state.host = host
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            history = await client.get(f"/api/sessions/{session_id}/history")
        assert history.status_code == 200
        persisted = next(record for record in history.json()["records"] if record["type"] == "run_ended")
        assert persisted["error"] == feedback
