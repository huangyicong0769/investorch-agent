import pytest


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
