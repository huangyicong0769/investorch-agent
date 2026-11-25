import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from agents import SQLiteSession

from investorch.application import open_application_host
from investorch.application.skills import SkillOperations
from investorch.commands import dispatch_command
from investorch.commands.parser import Command
from investorch.migration_cli import run_migration_cli
from investorch.storage.sessions import SessionFormatError, init_session_metadata, list_sessions
from tests.support.config import make_test_config


def read_database(path: Path) -> tuple:
    with closing(sqlite3.connect(path)) as connection:
        return (
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute("SELECT * FROM agent_sessions ORDER BY session_id").fetchall(),
            connection.execute("SELECT * FROM agent_messages ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM extra_session_metadata ORDER BY session_id").fetchall(),
        )


async def test_explicit_migration_prepends_once_and_preserves_legacy_rows(tmp_path, capsys, monkeypatch):
    config = make_test_config(tmp_path)
    with closing(sqlite3.connect(config.sessions_db)) as connection, connection:
        connection.execute("PRAGMA user_version = 0")
        connection.execute("INSERT INTO agent_sessions (session_id) VALUES ('empty')")
    legacy_history = [
        {"role": "developer", "content": "Application instruction for the original work."},
        {"role": "user", "content": "Previous question"},
        {"role": "assistant", "content": "Previous answer"},
    ]
    session = SQLiteSession("archived", config.sessions_db)
    try:
        await session.add_items(legacy_history)
    finally:
        session.close()
    with closing(sqlite3.connect(config.sessions_db)) as connection, connection:
        connection.execute(
            "INSERT INTO extra_session_metadata (session_id, title, archived_at) VALUES ('archived', 'Original', 'then')"
        )
    original = read_database(config.sessions_db)
    args = ["sessions", "--root", str(config.root)]

    with pytest.raises(SessionFormatError, match="investorch migrate sessions"):
        init_session_metadata(config.sessions_db)
    files = set(config.sessions_db.parent.glob("sessions.db*"))
    run_migration_cli([*args, "--dry-run"])
    preview = json.loads(capsys.readouterr().out)
    assert (preview["source_version"], preview["sessions"], preview["migrated_sessions"]) == (0, 2, 0)
    assert preview["backup_path"] is None
    assert set(config.sessions_db.parent.glob("sessions.db*")) == files
    assert read_database(config.sessions_db) == original

    run_migration_cli(args)
    migrated = json.loads(capsys.readouterr().out)
    assert migrated["migrated_sessions"] == 2
    assert read_database(Path(migrated["backup_path"])) == original
    version, identities, messages, metadata = read_database(config.sessions_db)
    assert version == 1
    assert identities == original[1] and metadata == original[3]
    assert messages[2:] == original[2]
    for session_id, expected_tail in (("archived", legacy_history), ("empty", [])):
        session = SQLiteSession(session_id, config.sessions_db)
        try:
            prompt, *tail = await session.get_items()
        finally:
            session.close()
        assert prompt["role"] == "developer"
        assert "You are InvestOrch Agent" in prompt["content"]
        assert tail == expected_tail

    def unavailable_catalog(_skills):
        raise ValueError("Unavailable Skill registry")

    with monkeypatch.context() as patch:
        patch.setattr(SkillOperations, "enabled_catalog", unavailable_catalog)
        run_migration_cli(args)
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["migrated_sessions"] == 0 and rerun["backup_path"] is None
    assert read_database(config.sessions_db) == (version, identities, messages, metadata)

    async def approve(*_):
        return True

    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        temporary = host.initial_session_id
        assert {item.session_id for item in list_sessions(config.sessions_db, include_archived=True)} >= {
            "archived",
            "empty",
        }
        target = await host.sessions.create()
        await dispatch_command(Command("resume", (target,)), host.state, runtime=host.runtime, sessions=host.sessions)
        assert host.state.selected_session_id == target
        assert temporary not in {item.session_id for item in list_sessions(config.sessions_db)}
    assert {item.session_id for item in list_sessions(config.sessions_db, include_archived=True)} == {
        "archived",
        "empty",
    }


def test_migration_preview_rejects_pending_wal(tmp_path, capsys):
    config = make_test_config(tmp_path)
    with closing(sqlite3.connect(config.sessions_db)) as writer:
        writer.execute("PRAGMA user_version = 0")
        writer.execute("INSERT INTO agent_sessions (session_id) VALUES ('pending')")
        writer.commit()
        wal = config.sessions_db.with_name("sessions.db-wal")
        assert wal.stat().st_size > 0
        with pytest.raises(SystemExit) as error:
            run_migration_cli(["sessions", "--root", str(config.root), "--dry-run"])
        assert error.value.code == 1
        assert "pending WAL data" in capsys.readouterr().err
        assert writer.execute("SELECT session_id FROM agent_sessions").fetchall() == [("pending",)]
