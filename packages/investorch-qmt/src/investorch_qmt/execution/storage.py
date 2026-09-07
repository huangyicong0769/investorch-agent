"""Durable SQLite boundary; each transaction obtains its own connection."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schema import SCHEMA


class RuntimeStorage:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("runtime database has a newer schema")
            if version == 0:
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version = 1")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA busy_timeout = 5000")
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
