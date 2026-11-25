"""Explicit, offline migration to Sessions with a stored system prompt."""

import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkstemp

from .sessions import SESSION_SCHEMA_VERSION, SessionFormatError, require_session_format


@dataclass(frozen=True, slots=True)
class SessionMigrationResult:
    source_version: int
    target_version: int
    sessions: int
    migrated_sessions: int = 0
    backup_path: str | None = None


def _backup_session_database(path: Path) -> Path:
    descriptor, name = mkstemp(prefix=f"{path.name}.pre-v{SESSION_SCHEMA_VERSION}-", suffix=".bak", dir=path.parent)
    os.close(descriptor)
    backup = Path(name)
    try:
        with (
            closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(backup)) as destination,
        ):
            source.backup(destination)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def migrate_sessions(
    db_path: Path, *, get_system_prompt: Callable[[], str], dry_run: bool = False
) -> SessionMigrationResult:
    path = db_path.resolve(strict=True)
    uri = path.as_uri() + "?mode=rw"
    if dry_run:
        wal = path.with_name(path.name + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise SessionFormatError("Session database has pending WAL data. Preview a checkpointed SQLite backup.")
        uri = path.as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection, connection:
        connection.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != 0:
            require_session_format(connection)
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SessionFormatError("Session database integrity check failed.")
        connection.execute("SELECT id, session_id, message_data, created_at FROM agent_messages LIMIT 0")
        count = connection.execute("SELECT count(*) FROM agent_sessions").fetchone()[0]
        if dry_run or version == SESSION_SCHEMA_VERSION:
            return SessionMigrationResult(version, SESSION_SCHEMA_VERSION, count)
        system_prompt = get_system_prompt()
        if not system_prompt:
            raise ValueError("A Session requires a system prompt")

        backup = _backup_session_database(path)
        minimum_id = connection.execute("SELECT coalesce(min(id), 0) FROM agent_messages").fetchone()[0]
        connection.execute(
            """
            INSERT INTO agent_messages (id, session_id, message_data, created_at)
            SELECT ? - row_number() OVER (ORDER BY session_id), session_id, ?, created_at
            FROM agent_sessions
            """,
            (min(minimum_id, 0), json.dumps({"role": "developer", "content": system_prompt})),
        )
        connection.execute(f"PRAGMA user_version = {SESSION_SCHEMA_VERSION}")
        return SessionMigrationResult(version, SESSION_SCHEMA_VERSION, count, count, str(backup))
