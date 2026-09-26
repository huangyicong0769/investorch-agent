from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from investorch.portfolio.migration import migrate_portfolio
from investorch.portfolio.schema import LegacyLiveDeploymentActiveError, PortfolioSchemaError
from tests.support.portfolio_migration import load_legacy_portfolio_fixture, portfolio_semantic_snapshot


def _database(tmp_path: Path, version: int) -> Path:
    path = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(path, version)
    return path


def _rows(path: Path, table: str) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()


@pytest.mark.parametrize("version", range(1, 6))
def test_explicit_migration_preserves_economics_and_archives_verifiable_history(tmp_path: Path, version: int) -> None:
    path = _database(tmp_path, version)
    before = portfolio_semantic_snapshot(path)
    ledger = _rows(path, "portfolio_ledger")
    live = _rows(path, "live_deployments") if version >= 3 else []

    report = migrate_portfolio(path)

    assert report.safe
    assert (report.source_schema_version, report.target_schema_version) == (version, 6)
    assert report.portfolio_count == 1
    assert report.ledger_entry_count == len(ledger)
    assert report.live_deployment_count == len(live)
    assert portfolio_semantic_snapshot(path) == before
    assert _rows(path, "portfolio_ledger") == ([(*row, None) for row in ledger] if version == 1 else ledger)
    assert report.archive_path is not None
    archive = Path(report.archive_path)
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["source_schema_version"] == version
    assert manifest["target_schema_version"] == 6
    assert manifest["database_filename"] == path.name
    assert manifest["ledger_entry_count"] == len(ledger)
    assert manifest["live_deployment_count"] == len(live)
    if version >= 3:
        assert manifest["live_deployment_status_counts"] == {"STOPPED": 1, "FAILED": 1}
    checksums = {}
    for line in (archive / "SHA256SUMS").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        checksums[filename.lstrip("*")] = digest
    assert {"manifest.json", "portfolio.db.bak", "legacy_schema.sql", "live_deployments.jsonl"} <= checksums.keys()
    for filename, digest in checksums.items():
        assert hashlib.sha256((archive / filename).read_bytes()).hexdigest() == digest
    for filename, digest in manifest["artifacts"].items():
        assert digest.removeprefix("sha256:") == checksums[filename]
    backup = archive / "portfolio.db.bak"
    assert portfolio_semantic_snapshot(backup) == before
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    exported = [json.loads(line) for line in (archive / "live_deployments.jsonl").read_text().splitlines()]
    assert len(exported) == len(live)
    if live:
        assert _rows(backup, "live_deployments") == live
        with sqlite3.connect(backup) as connection:
            connection.row_factory = sqlite3.Row
            assert exported == [
                dict(row) for row in connection.execute("SELECT * FROM live_deployments ORDER BY deployment_id")
            ]
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'live_%'").fetchall() == []
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("version", range(1, 6))
def test_dry_run_leaves_database_and_directory_exactly_unchanged(tmp_path: Path, version: int) -> None:
    path = _database(tmp_path, version)
    before = path.read_bytes()
    files = set(tmp_path.rglob("*"))

    report = migrate_portfolio(path, dry_run=True)

    assert report.safe
    assert report.source_schema_version == version
    assert report.archive_path is not None
    assert report.planned_schema_changes
    assert path.read_bytes() == before
    assert set(tmp_path.rglob("*")) == files


@pytest.mark.parametrize("version", (3, 4, 5))
@pytest.mark.parametrize("status", ("PREPARED", "ACTIVE"))
def test_nonterminal_deployment_blocks_dry_run_and_execution_without_writes(
    tmp_path: Path, version: int, status: str
) -> None:
    path = _database(tmp_path, version)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE live_deployments SET status = ? WHERE deployment_id = 'deployment-stopped'", (status,)
        )
    before = path.read_bytes()
    files = set(tmp_path.rglob("*"))

    report = migrate_portfolio(path, dry_run=True)
    assert not report.safe
    assert report.reason
    with pytest.raises(LegacyLiveDeploymentActiveError):
        migrate_portfolio(path)

    assert path.read_bytes() == before
    assert set(tmp_path.rglob("*")) == files


def test_already_migrated_database_is_noop_without_another_archive(tmp_path: Path) -> None:
    path = _database(tmp_path, 5)
    migrate_portfolio(path)
    before = path.read_bytes()
    files = set(tmp_path.rglob("*"))

    report = migrate_portfolio(path)

    assert report.safe
    assert report.source_schema_version == report.target_schema_version == 6
    assert report.archive_path is None
    assert path.read_bytes() == before
    assert set(tmp_path.rglob("*")) == files


@pytest.mark.parametrize("failure", ("backup", "archive", "postflight", "version"))
def test_system_boundary_failures_preserve_entire_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = _database(tmp_path, 5)
    before = path.read_bytes()
    original_connect = sqlite3.connect
    original_write = Path.write_text
    hit = []

    class FailingConnection(sqlite3.Connection):
        def backup(self, target: sqlite3.Connection, **kwargs: object) -> None:
            if failure == "backup":
                hit.append(failure)
                raise sqlite3.OperationalError("injected backup failure")
            super().backup(target, **kwargs)

        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
            normalized = " ".join(sql.upper().split())
            if (failure == "postflight" and self.in_transaction and normalized == "PRAGMA QUICK_CHECK") or (
                failure == "version" and normalized.replace(" ", "") == "PRAGMAUSER_VERSION=6"
            ):
                hit.append(failure)
                raise sqlite3.OperationalError(f"injected {failure} failure")
            return super().execute(sql, parameters)

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = FailingConnection
        return original_connect(*args, **kwargs)

    def write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if failure == "archive" and self.name == "manifest.json":
            hit.append(failure)
            raise OSError("injected archive failure")
        return original_write(self, data, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        patch.setattr(Path, "write_text", write_text)
        with pytest.raises((PortfolioSchemaError, sqlite3.Error, OSError)):
            migrate_portfolio(path)

    assert hit == [failure]
    assert path.read_bytes() == before
    assert len(_rows(path, "live_deployments")) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' AND name LIKE 'live_%'"
        ).fetchall() == [("live_deployment_active_portfolio",), ("live_trade_external_identity",)]


@pytest.mark.parametrize("invalid", ("future", "unversioned", "foreign_key", "projection"))
def test_invalid_legacy_database_is_rejected_before_archive_or_mutation(tmp_path: Path, invalid: str) -> None:
    path = _database(tmp_path, 5)
    with sqlite3.connect(path) as connection:
        if invalid == "future":
            connection.execute("PRAGMA user_version = 7")
        elif invalid == "unversioned":
            connection.execute("PRAGMA user_version = 0")
        elif invalid == "foreign_key":
            connection.execute("UPDATE portfolio_ledger SET broker_account_id = 'missing-account' WHERE sequence = 8")
        else:
            connection.execute("UPDATE portfolio_cash SET amount = '0'")
    before = path.read_bytes()
    files = set(tmp_path.rglob("*"))

    with pytest.raises(PortfolioSchemaError):
        migrate_portfolio(path)

    assert path.read_bytes() == before
    assert set(tmp_path.rglob("*")) == files
