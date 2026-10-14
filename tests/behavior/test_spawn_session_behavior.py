from pathlib import Path

import pytest

from investorch.storage import (
    add_session_related_portfolio_ids,
    create_session,
    create_spawn_session,
    delete_unused_session,
    get_session,
    get_session_related_portfolio_ids,
    init_session_metadata,
    list_sessions,
)
from tests.support.config import make_test_config
from tests.support.sessions import make_session_harness


def test_spawn_is_a_durable_normal_session_with_a_portfolio_snapshot(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "parent")
    add_session_related_portfolio_ids(config.sessions_db, "parent", ("core", "growth"))

    assert create_spawn_session(
        config.sessions_db,
        "child",
        parent_session_id="parent",
        parent_run_id="run",
        tool_call_id="call",
        title="Research",
    )
    add_session_related_portfolio_ids(config.sessions_db, "parent", ("later",))
    init_session_metadata(config.sessions_db)

    child = get_session(config.sessions_db, "child")
    assert child.title == "Research"
    assert child.spawned_by_session_id == "parent"
    assert child.spawned_by_run_id == "run"
    assert child.branch_from_session_id is None
    assert child.archived_at is None
    assert get_session_related_portfolio_ids(config.sessions_db, "child") == ("core", "growth")
    assert {record.session_id for record in list_sessions(config.sessions_db)} == {"parent", "child"}
    assert get_session(config.sessions_db, "parent").spawned_by_session_id is None


def test_repeated_spawn_creation_preserves_the_original_snapshot(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "parent")
    identity = {"parent_session_id": "parent", "parent_run_id": "run", "tool_call_id": "call"}
    create_spawn_session(config.sessions_db, "child", **identity)
    add_session_related_portfolio_ids(config.sessions_db, "parent", ("later",))

    assert create_spawn_session(config.sessions_db, "child", title="Changed", **identity) is False
    assert get_session_related_portfolio_ids(config.sessions_db, "child") == ()
    assert not get_session(config.sessions_db, "child").title
    assert delete_unused_session(config.sessions_db, "child") is False
    assert delete_unused_session(config.sessions_db, "parent") is False


def test_invalid_spawn_parent_does_not_create_a_session(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    with pytest.raises(KeyError):
        create_spawn_session(
            config.sessions_db, "child", parent_session_id="missing", parent_run_id="run", tool_call_id="call"
        )
    assert get_session(config.sessions_db, "child") is None


async def test_fork_of_a_spawn_is_an_independent_fork(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    parent = await harness.operations.create()
    create_spawn_session(
        harness.runtime.config.sessions_db, "child", parent_session_id=parent, parent_run_id="run", tool_call_id="call"
    )
    fork = await harness.operations.fork("child")
    record = get_session(harness.runtime.config.sessions_db, fork)
    assert record.branch_from_session_id == "child"
    assert record.spawned_by_session_id is None
    await harness.runtime.runtime.aclose()
