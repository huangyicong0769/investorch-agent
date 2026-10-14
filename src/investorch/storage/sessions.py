from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
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
    spawned_by_session_id: str | None = None
    spawned_by_run_id: str | None = None


class SessionMetadataError(RuntimeError):
    pass


class SessionDeletionScopeChangedError(SessionMetadataError):
    pass


class SessionExternalForkError(SessionMetadataError):
    pass


@dataclass(frozen=True)
class SessionDeletionScope:
    session_ids: tuple[str, ...]
    external_fork_ids: tuple[str, ...]


def _deletion_scope(connection: sqlite3.Connection, session_id: str) -> SessionDeletionScope:
    existing = {row[0] for row in connection.execute("SELECT session_id FROM agent_sessions")}
    if session_id not in existing:
        raise KeyError(session_id)
    children: dict[str, list[str]] = {}
    for child, parent in connection.execute("SELECT session_id, parent_session_id FROM session_spawn_lineage"):
        if child not in existing or parent not in existing or child == parent:
            raise SessionMetadataError("Invalid Spawn lineage")
        children.setdefault(parent, []).append(child)
    closure: set[str] = set()
    pending = [session_id]
    while pending:
        current = pending.pop()
        if current in closure:
            raise SessionMetadataError("Cyclic Spawn lineage")
        closure.add(current)
        pending.extend(children.get(current, ()))
    forks = tuple(
        sorted(
            child
            for child, parent in connection.execute("SELECT session_id, branch_from_session_id FROM session_lineage")
            if parent in closure and child not in closure
        )
    )
    return SessionDeletionScope(tuple(sorted(closure)), forks)


def get_session_deletion_scope(db_path: str | Path, session_id: str) -> SessionDeletionScope:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("BEGIN")
        return _deletion_scope(connection, session_id)


def create_session(db_path: str | Path, session_id: str) -> bool:
    session = SQLiteSession(session_id, db_path)
    session.close()
    with closing(sqlite3.connect(db_path)) as connection:
        cursor = connection.execute("INSERT OR IGNORE INTO agent_sessions (session_id) VALUES (?)", (session_id,))
        connection.commit()
    return cursor.rowcount == 1


def create_spawn_session(
    db_path: str | Path,
    session_id: str,
    *,
    parent_session_id: str,
    parent_run_id: str,
    tool_call_id: str,
    title: str | None = None,
) -> bool:
    """Atomically create an ordinary Session with trusted provenance and a Portfolio snapshot."""
    if not all((session_id, parent_session_id, parent_run_id, tool_call_id)) or session_id == parent_session_id:
        raise ValueError("Invalid Spawn identity")
    with closing(sqlite3.connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            parent = connection.execute(
                """
                SELECT metadata.archived_at, metadata.related_portfolio_ids_json
                FROM agent_sessions AS sessions
                LEFT JOIN extra_session_metadata AS metadata ON metadata.session_id = sessions.session_id
                WHERE sessions.session_id = ?
                """,
                (parent_session_id,),
            ).fetchone()
            if parent is None or parent[0] is not None:
                raise KeyError(parent_session_id)
            existing = connection.execute(
                "SELECT parent_session_id, parent_run_id, tool_call_id FROM session_spawn_lineage WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            identity = (parent_session_id, parent_run_id, tool_call_id)
            if existing == identity:
                if connection.execute("SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,)).fetchone():
                    return False
                raise SessionMetadataError("Missing Spawn Session")
            if (
                existing is not None
                or connection.execute("SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,)).fetchone()
            ):
                raise SessionMetadataError("Conflicting Spawn identity")
            related = _decode_related_portfolio_ids(parent[1])
            connection.execute("INSERT INTO agent_sessions (session_id) VALUES (?)", (session_id,))
            connection.execute("INSERT INTO session_spawn_lineage VALUES (?, ?, ?, ?)", (session_id, *identity))
            connection.execute(
                "INSERT INTO extra_session_metadata (session_id, title, related_portfolio_ids_json) VALUES (?, ?, ?)",
                (session_id, title or "", _encode_related_portfolio_ids(related)),
            )
            connection.commit()
            return True
        except BaseException:
            connection.rollback()
            raise


def session_exists(db_path: str | Path, session_id: str) -> bool:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute("SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,)).fetchone()
    return row is not None


def delete_unstarted_spawn_session(db_path: str | Path, session_id: str) -> bool:
    """Rollback only an unstarted Spawn; ordinary unused cleanup never removes lineage."""
    with closing(sqlite3.connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            eligible = connection.execute(
                """
                SELECT 1 FROM session_spawn_lineage WHERE session_id = ?
                    AND NOT EXISTS (SELECT 1 FROM agent_messages WHERE session_id = ?)
                    AND NOT EXISTS (SELECT 1 FROM extra_session_metadata
                        WHERE session_id = ? AND application_workflow_started != 0)
                    AND NOT EXISTS (SELECT 1 FROM session_spawn_lineage WHERE parent_session_id = ?)
                    AND NOT EXISTS (SELECT 1 FROM session_lineage WHERE branch_from_session_id = ? OR session_id = ?)
                """,
                (session_id,) * 6,
            ).fetchone()
            if eligible is None:
                return False
            connection.execute("DELETE FROM session_spawn_lineage WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM extra_session_metadata WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
            connection.commit()
            return True
        except BaseException:
            connection.rollback()
            raise


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
                AND NOT EXISTS (
                    SELECT 1 FROM session_spawn_lineage
                    WHERE session_spawn_lineage.session_id = agent_sessions.session_id
                        OR session_spawn_lineage.parent_session_id = agent_sessions.session_id
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
    expected_session_ids: tuple[str, ...] | None = None,
) -> None:
    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Session deletion cancelled before commit")

    with closing(sqlite3.connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            check_cancelled()
            scope = _deletion_scope(connection, session_id)
            expected = tuple(sorted(expected_session_ids or (session_id,)))
            if scope.session_ids != expected:
                raise SessionDeletionScopeChangedError("Spawn descendants changed before deletion")
            if scope.external_fork_ids:
                raise SessionExternalForkError("Independent Fork branches refer to the deletion scope")
            placeholders = ",".join("?" for _ in expected)
            for table in (
                "agent_messages",
                "extra_session_metadata",
                "session_lineage",
                "session_spawn_lineage",
                "agent_sessions",
            ):
                connection.execute(f"DELETE FROM {table} WHERE session_id IN ({placeholders})", expected)
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
                archived_at TEXT,
                related_portfolio_ids_json TEXT,
                application_workflow_started INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(extra_session_metadata)")}
        if "archived_at" not in columns:
            connection.execute("ALTER TABLE extra_session_metadata ADD COLUMN archived_at TEXT")
        if "related_portfolio_ids_json" not in columns:
            connection.execute("ALTER TABLE extra_session_metadata ADD COLUMN related_portfolio_ids_json TEXT")
        if "application_workflow_started" not in columns:
            connection.execute(
                "ALTER TABLE extra_session_metadata ADD COLUMN application_workflow_started INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_lineage (
                session_id TEXT PRIMARY KEY,
                branch_from_session_id TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_spawn_lineage (
                session_id TEXT PRIMARY KEY,
                parent_session_id TEXT NOT NULL CHECK (parent_session_id != session_id),
                parent_run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                UNIQUE(parent_session_id, parent_run_id, tool_call_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_spawn_lineage_parent ON session_spawn_lineage(parent_session_id)"
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
                sessions.updated_at,
                spawn.parent_session_id,
                spawn.parent_run_id
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            LEFT JOIN session_lineage AS lineage
                ON lineage.session_id = sessions.session_id
            LEFT JOIN session_spawn_lineage AS spawn ON spawn.session_id = sessions.session_id
            {archived_filter}
            ORDER BY sessions.updated_at DESC
            """
        ).fetchall()

    return [
        SessionRecord(
            session_id=row[0],
            title=row[1],
            branch_from_session_id=row[2],
            archived_at=row[3],
            created_at=row[4],
            updated_at=row[5],
            spawned_by_session_id=row[6],
            spawned_by_run_id=row[7],
        )
        for row in rows
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
                sessions.updated_at,
                spawn.parent_session_id,
                spawn.parent_run_id
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            LEFT JOIN session_lineage AS lineage
                ON lineage.session_id = sessions.session_id
            LEFT JOIN session_spawn_lineage AS spawn ON spawn.session_id = sessions.session_id
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
        spawned_by_session_id=row[6],
        spawned_by_run_id=row[7],
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


def get_session_related_portfolio_ids(db_path: str | Path, session_id: str) -> tuple[str, ...]:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT metadata.related_portfolio_ids_json
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            WHERE sessions.session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        raise KeyError(session_id)
    return _decode_related_portfolio_ids(row[0])


def is_application_workflow_started(db_path: str | Path, session_id: str) -> bool:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT metadata.application_workflow_started
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            WHERE sessions.session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        raise KeyError(session_id)
    return row[0] == 1


def mark_application_workflow_started(db_path: str | Path, session_id: str) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        if connection.execute("SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,)).fetchone() is None:
            raise KeyError(session_id)
        connection.execute(
            """
            INSERT INTO extra_session_metadata (session_id, title, application_workflow_started)
            VALUES (?, '', 1)
            ON CONFLICT(session_id) DO UPDATE
            SET application_workflow_started = 1
            """,
            (session_id,),
        )
        connection.commit()


def add_session_related_portfolio_ids(
    db_path: str | Path,
    session_id: str,
    portfolio_ids: Iterable[str],
) -> tuple[str, ...]:
    additions = _normalize_related_portfolio_ids(portfolio_ids)
    with closing(sqlite3.connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT metadata.related_portfolio_ids_json
                FROM agent_sessions AS sessions
                LEFT JOIN extra_session_metadata AS metadata
                    ON metadata.session_id = sessions.session_id
                WHERE sessions.session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            existing = _decode_related_portfolio_ids(row[0])
            combined = _normalize_related_portfolio_ids((*existing, *additions))
            if combined != existing:
                connection.execute(
                    """
                    INSERT INTO extra_session_metadata (session_id, title, related_portfolio_ids_json)
                    VALUES (?, '', ?)
                    ON CONFLICT(session_id) DO UPDATE
                    SET related_portfolio_ids_json = excluded.related_portfolio_ids_json
                    """,
                    (session_id, _encode_related_portfolio_ids(combined)),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return combined


def copy_session_related_portfolio_ids(
    db_path: str | Path,
    source_session_id: str,
    target_session_id: str,
) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT sessions.session_id, metadata.related_portfolio_ids_json
                FROM agent_sessions AS sessions
                LEFT JOIN extra_session_metadata AS metadata
                    ON metadata.session_id = sessions.session_id
                WHERE sessions.session_id IN (?, ?)
                """,
                (source_session_id, target_session_id),
            ).fetchall()
            values = {row[0]: row[1] for row in rows}
            if source_session_id not in values:
                raise KeyError(source_session_id)
            if target_session_id not in values:
                raise KeyError(target_session_id)
            related = _decode_related_portfolio_ids(values[source_session_id])
            if related:
                connection.execute(
                    """
                    INSERT INTO extra_session_metadata (session_id, title, related_portfolio_ids_json)
                    VALUES (?, '', ?)
                    ON CONFLICT(session_id) DO UPDATE
                    SET related_portfolio_ids_json = excluded.related_portfolio_ids_json
                    """,
                    (target_session_id, _encode_related_portfolio_ids(related)),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _decode_related_portfolio_ids(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SessionMetadataError("Malformed related Portfolio metadata") from exc
    if not isinstance(decoded, list):
        raise SessionMetadataError("Malformed related Portfolio metadata: expected a JSON array")
    try:
        return _normalize_related_portfolio_ids(decoded)
    except ValueError as exc:
        raise SessionMetadataError("Malformed related Portfolio metadata: expected non-empty string IDs") from exc


def _normalize_related_portfolio_ids(portfolio_ids: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for portfolio_id in portfolio_ids:
        if not isinstance(portfolio_id, str) or not portfolio_id:
            raise ValueError("Related Portfolio IDs must be non-empty strings")
        if portfolio_id not in seen:
            normalized.append(portfolio_id)
            seen.add(portfolio_id)
    return tuple(normalized)


def _encode_related_portfolio_ids(portfolio_ids: tuple[str, ...]) -> str:
    return json.dumps(portfolio_ids, ensure_ascii=False, separators=(",", ":"))


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
