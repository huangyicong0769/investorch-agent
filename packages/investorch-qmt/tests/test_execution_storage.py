import sqlite3

import pytest

from investorch_qmt.execution.storage import RuntimeStorage


def test_runtime_database_reopens_and_rejects_newer_schema(tmp_path):
    path = tmp_path / "runtime.db"
    RuntimeStorage(path)
    RuntimeStorage(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(ValueError, match="newer"):
        RuntimeStorage(path)
