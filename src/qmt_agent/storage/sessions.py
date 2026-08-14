import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    created_at: str
    updated_at: str


def list_sessions(db_path: str | Path,) -> list[SessionRecord]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                session_id,
                created_at,
                updated_at
            FROM agent_sessions
            ORDER BY updated_at DESC
            """
        ).fetchall()

    return [
        SessionRecord(
            session_id=row[0],
            created_at=row[1],
            updated_at=row[2],
        )
        for row in rows
    ]


def session_exists(db_path: str | Path, session_id: str,) -> bool:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM agent_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()

    return row is not None