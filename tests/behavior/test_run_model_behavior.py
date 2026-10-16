from pathlib import Path

from investorch.storage import create_session


async def test_queued_run_uses_latest_draft_while_active_model_stays_frozen(tmp_path: Path) -> None:
    from tests.support.runtime import make_runtime_harness, run_options

    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "session")
    models = harness.runtime.session_models
    try:
        first = harness.runtime.start_run("session", "first", run_options("queue"))
        await harness.agent_loop.wait_until_started("session")
        initial = first.options.model
        await harness.runtime.submit_follow_up("session", "queued", run_options("steer"))
        models.update("session", reasoning_effort="low")
        assert first.options.model == initial
        assert models.view("session").persisted == initial
        harness.agent_loop.complete("session")
        await harness.agent_loop.wait_until_started("session", 2)
        promoted = harness.runtime.get_active_run("session")
        assert promoted.options.model.reasoning_effort == "low"
        assert promoted.options.follow_up_behavior == "steer"
        assert models.view("session").persisted == promoted.options.model
        assert models.view("session").draft is None
    finally:
        await harness.runtime.aclose()


async def test_metadata_commit_failure_keeps_draft_and_durable_input_without_running_main(tmp_path, monkeypatch):
    import pytest

    from investorch.journal import read_session_journal
    from tests.support.runtime import make_runtime_harness, run_options

    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "session")
    models = harness.runtime.session_models
    models.update("session", reasoning_effort="low")

    def disk_full(*_):
        raise OSError("controlled metadata disk full")

    monkeypatch.setattr("investorch.application.session_models.set_session_model", disk_full)
    active = harness.runtime.start_run("session", "Recorded once", run_options())
    try:
        with pytest.raises(RuntimeError, match="journaled but not executed"):
            await active.task
        records = read_session_journal(harness.config.session_journal_dir, "session")
        assert [item["text"] for item in records if item["type"] == "user_message"] == ["Recorded once"]
        with pytest.raises(AssertionError, match="No controlled run"):
            harness.agent_loop.input_for("session")
        assert models.view("session").draft.reasoning_effort == "low"
        assert models.view("session").persisted is None
        assert harness.runtime.get_active_run("session") is None
    finally:
        await harness.runtime.aclose()
