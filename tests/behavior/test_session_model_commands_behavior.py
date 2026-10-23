import pytest

from investorch.commands.handlers import dispatch_command
from investorch.commands.parser import parse_command
from investorch.context import AppState
from tests.support.runtime import run_options
from tests.support.sessions import make_session_harness


async def test_model_and_effort_commands_change_only_selected_sessions_next_run(tmp_path):
    harness = make_session_harness(tmp_path)
    runtime = harness.runtime.runtime
    session = await harness.operations.create()
    other = await harness.operations.create()
    state = AppState(harness.runtime.config, harness.runtime.execution, session, runtime.session_models)

    async def command(text):
        return await dispatch_command(parse_command(text), state, runtime=runtime, sessions=harness.operations)

    try:
        active = runtime.start_run(session, "running", run_options())
        await harness.runtime.agent_loop.wait_until_started(session)
        before = active.options.model
        await command("/effort low")
        assert runtime.session_models.view(session).draft.reasoning_effort == "low"
        assert active.options.model == before
        assert runtime.session_models.view(other).draft is None
        shown = (await command("/model")).output
        assert "Active:" in shown and "Saved:" in shown and "Next Run:" in shown
        assert "effort low" in shown
        await command("/model test replacement-model high 80000")
        assert runtime.session_models.view(session).draft.name == "replacement-model"
        assert runtime.session_models.view(session).persisted == before
        rejected = await command("/model test replacement-model high 0")
        assert "positive integer" in rejected.output
        assert runtime.session_models.view(session).draft.context_window_tokens == 80000
    finally:
        await runtime.aclose()


async def test_terminal_chatgpt_login_uses_the_existing_host_and_keeps_authorization_url_out_of_output(
    tmp_path, monkeypatch
):
    from tests.support.web import open_test_web

    async with open_test_web(tmp_path, {"models": {"chatgpt": {"type": "chatgpt", "credentials": "personal"}}}) as web:
        session_id = await web.host.sessions.create()
        web.host.state.selected_session_id = session_id
        web.host.session_models.update(
            session_id, model="chatgpt", name="account-model", reasoning_effort="high", context_window_tokens=200000
        )
        auth_calls = []
        browser_urls = []

        class Auth:
            async def start_browser_login(self, connection):
                auth_calls.append(("login", connection))
                return {
                    "authorization_url": "https://auth.example.test/authorize?state=private-attempt",
                    "state": "private-attempt",
                }

            async def logout(self, credential):
                auth_calls.append(("logout", credential))
                return {
                    "message": "Signed out locally; remote revocation was not confirmed. Disconnect the app in ChatGPT Settings."
                }

        web.host.state.chatgpt_auth = Auth()
        monkeypatch.setattr(
            "investorch.commands.handlers.webbrowser.open", lambda url: browser_urls.append(url) or True
        )
        result = await dispatch_command(
            parse_command("/model login"), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
        )
        assert "Browser authorization started" in result.output
        assert "private-attempt" not in result.output and "https://" not in result.output
        assert auth_calls == [("login", "chatgpt")]
        assert len(browser_urls) == 1
        assert web.host.runtime.list_active_runs() == []
        assert not await web.host.journal.session_exists(session_id)
        web.host.state.selected_session_id = None
        result = await dispatch_command(
            parse_command("/model logout chatgpt"), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
        )
        assert "Signed out locally" in result.output
        assert "Disconnect the app in ChatGPT Settings." in result.output
        assert auth_calls[-1] == ("logout", "personal")


@pytest.mark.parametrize("failure", ["auth", "quota", "budget"])
async def test_plain_app_recovers_from_missing_connection_and_safe_model_failures(tmp_path, monkeypatch, failure):
    from dataclasses import asdict

    from agents import ModelSettings
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    from investorch.agents import ContextBudgetError
    from investorch.app import run_app
    from investorch.journal.reader import read_session_journal
    from investorch.llm.models import ChatGPTInferenceError
    from investorch.llm.oauth import ChatGPTAuthError
    from investorch.storage import list_sessions, set_session_title
    from investorch.storage.sessions import set_session_model
    from investorch.ui import ConsoleUI
    from tests.support.config import make_test_config
    from tests.support.model_connections import install_host_models

    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    model = ScriptedModel(
        (
            ModelStep.raise_error(
                ChatGPTAuthError("login_required", "Sign in to ChatGPT again.")
                if failure == "auth"
                else ChatGPTInferenceError("usage_limit_reached", 429)
                if failure == "quota"
                else ContextBudgetError(2000, 1000)
            ),
            (assistant_message("recovered"),),
        )
    )
    install_host_models(
        monkeypatch, lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings())
    )
    inputs = iter(
        (
            "/effort low",
            "blocked before journal",
            "/model test test-model high 1000000",
            "expired auth",
            "successful retry",
            "/exit",
        )
    )
    output = []
    selected = []

    class Terminal(ConsoleUI):
        async def read_user_input(self):
            if not selected:
                session_id = list_sessions(config.sessions_db)[0].session_id
                selected.append(session_id)
                set_session_title(config.sessions_db, session_id, "Existing Session")
                set_session_model(
                    config.sessions_db, session_id, {**asdict(config.agent_model("main")), "model": "missing"}
                )
            return next(inputs)

        def write(self, text):
            output.append(text)

    monkeypatch.setattr("investorch.app.ConsoleUI", Terminal)
    monkeypatch.setattr("investorch.app.load_config", lambda: config)
    monkeypatch.setattr("investorch.app.configure_logging", lambda _: None)
    await run_app(plain=True)
    assert sum("missing" in text for text in output) == 2
    guidance = {"auth": "Sign in to ChatGPT again.", "quota": "Manage Usage", "budget": "larger window"}[failure]
    assert sum(guidance in text for text in output) == 1
    assert "Agent: recovered" in output
    model.assert_complete()
    records = read_session_journal(config.session_journal_dir, selected[0])
    assert not any(record.get("text") == "blocked before journal" for record in records)
    assert sum(record.get("text") == "expired auth" for record in records) == 1
    assert sum(record.get("text") == "successful retry" for record in records) == 1
