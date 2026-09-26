import json
import os
import subprocess
import sys
from pathlib import Path

from tests.support.portfolio_migration import load_legacy_portfolio_fixture, portfolio_semantic_snapshot


def _cli(db, *args):
    return subprocess.run(
        [sys.executable, "-m", "investorch", "migrate", "portfolio", "--database", str(db), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_migration_cli_dry_run_and_retirement_without_application_startup(tmp_path):
    db = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(db, 5)
    before = db.read_bytes()
    dry = _cli(db, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    report = json.loads(dry.stdout)
    assert report["source_schema_version"] == 5
    assert report["safe"]
    assert report["live_deployment_count"] == 2
    assert db.read_bytes() == before
    assert set(tmp_path.iterdir()) == {db}
    result = _cli(db)
    assert result.returncode == 0, result.stderr
    archive = Path(json.loads(result.stdout)["archive_path"])
    assert portfolio_semantic_snapshot(db) == portfolio_semantic_snapshot(archive / "portfolio.db.bak")


def test_migration_command_does_not_import_agent_web_qmt_or_initializer(tmp_path):
    db = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(db, 2)
    code = """
import sys
from investorch.cli import entrypoint
entrypoint()
for prefix in ('investorch.app', 'investorch.agents', 'investorch.web', 'investorch.qmt', 'investorch.initializer', 'agents'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", code, "migrate", "portfolio", "--database", str(db), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "OPENAI_API_KEY": ""},
    )
    assert result.returncode == 0, result.stderr


def test_migration_cli_failure_returns_nonzero_without_creating_database(tmp_path):
    db = tmp_path / "missing.db"
    result = _cli(db)
    assert result.returncode != 0
    assert "migration failed" in result.stderr.lower()
    assert not db.exists()


def test_wal_dry_run_fails_closed_without_creating_sidecars(tmp_path):
    import sqlite3
    from contextlib import closing

    db = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(db, 5)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = _cli(db, "--dry-run")
    assert result.returncode != 0
    assert "WAL" in result.stderr
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
