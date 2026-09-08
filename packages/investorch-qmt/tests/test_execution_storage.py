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


def test_runtime_foreign_keys_reject_orphan_facts(tmp_path):
    path = tmp_path / "runtime.db"
    storage = RuntimeStorage(path)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), storage.transaction() as db:
        db.execute("""INSERT INTO outbox(fact_id,deployment_id,fact_type,external_fact_id,payload_json,status,created_at)
                VALUES('fact','missing','TRADE_V1','trade','{}','PENDING','2026-09-08T00:00:00Z')""")
    with storage.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


def test_serve_reports_newer_runtime_schema_as_startup_error(tmp_path):
    from investorch_qmt.config import default_paths, initialize_config
    from investorch_qmt.server import ServiceError, run_service

    paths = default_paths(tmp_path)
    config = initialize_config(paths)
    with sqlite3.connect(paths.runtime_db) as db:
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(ServiceError, match="runtime"):
        run_service(config, paths)
