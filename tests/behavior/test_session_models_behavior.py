from pathlib import Path

from investorch.application.session_models import SessionModelStore
from investorch.storage import create_session
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
