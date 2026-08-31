from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents import SQLiteSession


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    title: str | None
    branch_from_session_id: str | None
    archived_at: str | None
    created_at: str
    updated_at: str


def create_session(db_path: str | Path, session_id: str) -> None:
    session = SQLiteSession(session_id, db_path)
    session.close()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("INSERT OR IGNORE INTO agent_sessions (session_id) VALUES (?)", (session_id,))
        connection.commit()


def session_exists(db_path: str | Path, session_id: str) -> bool:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute("SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,)).fetchone()
    return row is not None


def delete_unused_session(db_path: str | Path, session_id: str) -> bool:
    with closing(sqlite3.connect(db_path)) as connection:
        cursor = connection.execute(
            """
            DELETE FROM agent_sessions
            WHERE session_id = ?
                AND NOT EXISTS (
                    SELECT 1 FROM agent_messages
                    WHERE agent_messages.session_id = agent_sessions.session_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM extra_session_metadata
                    WHERE extra_session_metadata.session_id = agent_sessions.session_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM session_lineage
                    WHERE session_lineage.session_id = agent_sessions.session_id
                        OR session_lineage.branch_from_session_id = agent_sessions.session_id
                )
            """,
            (session_id,),
        )
        connection.commit()
    return cursor.rowcount == 1


def delete_session_transaction(
    db_path: str | Path,
    session_id: str,
    *,
    cancel_event: threading.Event | None = None,
    commit_lock: threading.Lock | None = None,
) -> None:
    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Session deletion cancelled before commit")

    with closing(sqlite3.connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            check_cancelled()
            connection.execute("DELETE FROM agent_messages WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM extra_session_metadata WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM session_lineage WHERE session_id = ?", (session_id,))
            if commit_lock is None:
                connection.commit()
            else:
                with commit_lock:
                    check_cancelled()
                    connection.commit()
        except BaseException:
            connection.rollback()
            raise


def init_session_metadata(db_path: str | Path) -> None:
    schema_session = SQLiteSession("schema-init", db_path)
    schema_session.close()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS extra_session_metadata (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                archived_at TEXT
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(extra_session_metadata)")}
        if "archived_at" not in columns:
            connection.execute("ALTER TABLE extra_session_metadata ADD COLUMN archived_at TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_lineage (
                session_id TEXT PRIMARY KEY,
                branch_from_session_id TEXT NOT NULL
            )
            """
        )
        connection.commit()


def list_sessions(db_path: str | Path, *, include_archived: bool = False) -> list[SessionRecord]:
    archived_filter = "" if include_archived else "WHERE metadata.archived_at IS NULL"
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            f"""
            SELECT
                sessions.session_id,
                metadata.title,
                lineage.branch_from_session_id,
                metadata.archived_at,
                sessions.created_at,
                sessions.updated_at
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            LEFT JOIN session_lineage AS lineage
                ON lineage.session_id = sessions.session_id
            {archived_filter}
            ORDER BY sessions.updated_at DESC
            """
        ).fetchall()

    return [
        SessionRecord(session_id=row[0], title=row[1], branch_from_session_id=row[2], archived_at=row[3], created_at=row[4], updated_at=row[5]) for row in rows
    ]


def get_session(db_path: str | Path, session_id: str) -> SessionRecord | None:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT
                sessions.session_id,
                metadata.title,
                lineage.branch_from_session_id,
                metadata.archived_at,
                sessions.created_at,
                sessions.updated_at
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            LEFT JOIN session_lineage AS lineage
                ON lineage.session_id = sessions.session_id
            WHERE sessions.session_id = ?
            """,
            (session_id,),
        ).fetchone()

    if row is None:
        return None
    return SessionRecord(
        session_id=row[0],
        title=row[1],
        branch_from_session_id=row[2],
        archived_at=row[3],
        created_at=row[4],
        updated_at=row[5],
    )


def find_session_ids(db_path: str | Path, session_id_prefix: str) -> list[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT sessions.session_id
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            WHERE substr(sessions.session_id, 1, ?) = ?
                AND metadata.archived_at IS NULL
            ORDER BY sessions.updated_at DESC
            """,
            (len(session_id_prefix), session_id_prefix),
        ).fetchall()

    return [row[0] for row in rows]


def archive_session(db_path: str | Path, session_id: str) -> None:
    archived_at = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO extra_session_metadata (session_id, title, archived_at)
            VALUES (?, '', ?)
            ON CONFLICT(session_id) DO UPDATE
            SET archived_at = excluded.archived_at
            """,
            (session_id, archived_at),
        )
        connection.commit()


def unarchive_session(db_path: str | Path, session_id: str) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            UPDATE extra_session_metadata
            SET archived_at = NULL
            WHERE session_id = ?
            """,
            (session_id,),
        )
        connection.commit()


def is_session_archived(db_path: str | Path, session_id: str) -> bool:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT archived_at
            FROM extra_session_metadata
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    return row is not None and row[0] is not None


def list_archived_sessions(db_path: str | Path) -> list[SessionRecord]:
    return [record for record in list_sessions(db_path, include_archived=True) if record.archived_at is not None]


def get_session_title(db_path: str | Path, session_id: str) -> str | None:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT title
            FROM extra_session_metadata
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    return row[0] if row else None


def set_session_title(db_path: str | Path, session_id: str, title: str) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO extra_session_metadata (session_id, title)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET title = excluded.title
            """,
            (session_id, title),
        )
        connection.commit()


def get_session_branch_from(db_path: str | Path, session_id: str) -> str | None:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT branch_from_session_id
            FROM session_lineage
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    return row[0] if row else None


def session_has_children(db_path: str | Path, session_id: str) -> bool:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM session_lineage
            WHERE branch_from_session_id = ?
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    return row is not None


def set_session_branch_from(db_path: str | Path, session_id: str, branch_from_session_id: str) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO session_lineage (session_id, branch_from_session_id)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE
            SET branch_from_session_id = excluded.branch_from_session_id
            """,
            (session_id, branch_from_session_id),
        )
        connection.commit()


def delete_session_metadata(db_path: str | Path, session_id: str) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            DELETE FROM extra_session_metadata
            WHERE session_id = ?
            """,
            (session_id,),
        )
        connection.execute(
            """
            DELETE FROM session_lineage
            WHERE session_id = ?
            """,
            (session_id,),
        )
        connection.commit()
