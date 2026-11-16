from pathlib import Path

from investorch.application.session_models import SessionModelStore
from investorch.storage import create_session
from investorch.storage.sessions import set_session_model
from tests.support.config import make_test_config


async def test_session_model_edits_are_drafts_until_committed_and_survive_only_after_run(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session")
    models = SessionModelStore(config)
    initial = models.view("session").effective
    changed = models.update("session", reasoning_effort="low")
    assert changed.reasoning_effort == "low"
    assert models.view("session").persisted is None
    assert SessionModelStore(config).view("session").effective == initial
    snapshot = models.snapshot("session")
    models.update("session", reasoning_effort="high")
    await models.commit("session", snapshot)
    assert models.view("session").draft.reasoning_effort == "high"
    assert models.view("session").persisted == changed
    assert SessionModelStore(config).view("session").effective == changed


async def test_fork_copies_saved_and_draft_models_and_delete_cleans_draft(tmp_path: Path) -> None:
    from tests.support.sessions import make_session_harness

    harness = make_session_harness(tmp_path)
    models = harness.runtime.runtime.session_models
    parent = await harness.operations.create()
    models.update(parent, reasoning_effort="low")
    await models.commit(parent, models.snapshot(parent))
    models.update(parent, reasoning_effort="high")
    child = await harness.operations.fork(parent)
    assert models.view(child).persisted.reasoning_effort == "low"
    assert models.view(child).draft.reasoning_effort == "high"
    assert SessionModelStore(harness.runtime.config).view(child).effective.reasoning_effort == "low"
    await harness.operations.archive(child)
    assert models.view(child).draft.reasoning_effort == "high"
    await harness.operations.unarchive(child)
    await harness.operations.delete(child)
    assert models.draft(child) is None
    assert models.view(child).persisted is None
    await harness.runtime.runtime.aclose()


def test_legacy_model_uses_configured_deepseek_controls_without_rewriting_its_model_id(tmp_path: Path) -> None:
    config = make_test_config(tmp_path, {"agents": {"main": {"web_search": False, "safety_check": "high"}}})
    create_session(config.sessions_db, "session")
    original = {
        "model": "test",
        "name": "deepseek-v4-flash",
        "reasoning_effort": "low",
        "context_window_tokens": 1048576,
    }
    set_session_model(config.sessions_db, "session", original)
    models = SessionModelStore(config)
    snapshot = models.snapshot("session")
    assert snapshot.model.name == "deepseek-v4-flash" and snapshot.model.web_search is False
    set_session_model(config.sessions_db, "session", {**original, "web_search": True, "safety_check": "low"})
    assert models.view("session").persisted.safety_check == "low"
    updated = models.snapshot("session").model
    assert updated.web_search is True and updated.safety_check == "high"
