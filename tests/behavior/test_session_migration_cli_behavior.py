import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from agents import SQLiteSession

from investorch.migration_cli import run_migration_cli
from investorch.storage.sessions import SessionFormatError, init_session_metadata
from tests.support.config import make_test_config


def read_database(path: Path) -> tuple:
    with closing(sqlite3.connect(path)) as connection:
        return (
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute("SELECT * FROM agent_sessions ORDER BY session_id").fetchall(),
            connection.execute("SELECT * FROM agent_messages ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM extra_session_metadata ORDER BY session_id").fetchall(),
        )


async def test_explicit_migration_prepends_once_and_preserves_legacy_rows(tmp_path, capsys):
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
    run_migration_cli([*args, "--dry-run"])
    preview = json.loads(capsys.readouterr().out)
    assert (preview["source_version"], preview["sessions"], preview["migrated_sessions"]) == (0, 2, 0)
    assert preview["backup_path"] is None
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

    run_migration_cli(args)
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["migrated_sessions"] == 0 and rerun["backup_path"] is None
    assert read_database(config.sessions_db) == (version, identities, messages, metadata)
