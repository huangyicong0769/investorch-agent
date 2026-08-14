import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    title: str | None
    created_at: str
    updated_at: str


def init_session_metadata(db_path: str | Path,) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS extra_session_metadata (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL
            )
            """
        )
        connection.commit()


def list_sessions(db_path: str | Path,) -> list[SessionRecord]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                sessions.session_id,
                metadata.title,
                sessions.created_at,
                sessions.updated_at
            FROM agent_sessions AS sessions
            LEFT JOIN extra_session_metadata AS metadata
                ON metadata.session_id = sessions.session_id
            ORDER BY sessions.updated_at DESC
            """
        ).fetchall()

    return [
        SessionRecord(
            session_id=row[0],
            title=row[1],
            created_at=row[2],
            updated_at=row[3],
        )
        for row in rows
    ]


def find_session_ids(db_path: str | Path, session_id_prefix: str,) -> list[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT session_id
            FROM agent_sessions
            WHERE substr(session_id, 1, ?) = ?
            ORDER BY updated_at DESC
            """,
            (
                len(session_id_prefix),
                session_id_prefix,
            )
        ).fetchall()

    return [row[0] for row in rows]

def get_session_title(db_path: str | Path, session_id: str,) -> str | None:
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


def set_session_title(db_path: str | Path, session_id: str, title: str,) -> None:
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


def delete_session_metadata(db_path: str | Path, session_id: str,) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            DELETE FROM extra_session_metadata
            WHERE session_id = ?
            """,
            (session_id,),
        )
        connection.commit()