"""Explicit, recoverable Portfolio schema migration without application startup."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from investorch.portfolio.schema import (
    LegacyLiveDeploymentActiveError,
    PortfolioSchemaError,
    schema_version,
    upgrade_schema,
    validate_schema,
)


@dataclass(frozen=True)
class MigrationReport:
    database_path: str
    source_schema_version: int
    target_schema_version: int
    portfolio_count: int
    ledger_entry_count: int
    broker_count: int
    broker_account_count: int
    live_deployment_count: int
    live_deployment_status_counts: dict[str, int]
    safe: bool
    reason: str | None
    archive_path: str | None
    planned_schema_changes: tuple[str, ...]
    validation_summary: str


def _report(connection: sqlite3.Connection, path: Path, version: int) -> MigrationReport:
    def count(table: str) -> int:
        return connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    statuses = (
        dict(connection.execute("SELECT status, COUNT(*) FROM live_deployments GROUP BY status"))
        if version in (3, 4, 5)
        else {}
    )
    active = any(status not in ("STOPPED", "FAILED") for status in statuses)
    reason = (
        "PREPARED/ACTIVE legacy deployment remains. Use the old version to stop/clean legacy deployments; "
        "ensure the old QMT companion / execution node and automated QMT strategies are stopped. "
        "Migration cannot assume remote execution has stopped."
        if active
        else None
    )
    archive = None
    if version != 6:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        archive = str(path.parent / "migration-archives" / f"portfolio-v{version}-to-v6" / stamp)
    changes = ()
    if version == 0:
        changes = ("Create canonical Portfolio v6",)
    elif version == 1:
        changes = ("Add BrokerAccount attribution; copy projections to NULL location", "Set schema version 6")
    elif version == 2:
        changes = ("Preserve all data; set schema version 6",)
    elif version in (3, 4, 5):
        changes = ("Archive terminal legacy deployments", "Drop legacy live table and indexes", "Set schema version 6")
    return MigrationReport(
        str(path),
        version,
        6,
        count("portfolios") if version else 0,
        count("portfolio_ledger") if version else 0,
        count("brokers") if version >= 2 else 0,
        count("broker_accounts") if version >= 2 else 0,
        sum(statuses.values()),
        statuses,
        not active,
        reason,
        archive,
        changes,
        "schema shape, foreign_key_check, quick_check, aggregate/account projections: passed",
    )


def _fingerprint(connection: sqlite3.Connection) -> str:
    """Detect any source change between the archived snapshot and write lock."""
    digest = hashlib.sha256()
    for line in connection.iterdump():
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    digest.update(str(schema_version(connection)).encode())
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _write_archive(connection: sqlite3.Connection, report: MigrationReport) -> None:
    """Publish the complete archive atomically before touching source schema."""
    from dataclasses import asdict

    assert report.archive_path is not None
    final = Path(report.archive_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=final.parent))
    backup = temporary / "portfolio.db.bak"
    backup.touch(mode=0o600)
    with closing(sqlite3.connect(backup)) as destination:
        connection.backup(destination)
        validate_schema(destination, report.source_schema_version)
        if _fingerprint(destination) != _fingerprint(connection):
            raise PortfolioSchemaError("Portfolio backup does not match the source snapshot")
    rows = []
    if report.source_schema_version in (3, 4, 5):
        cursor = connection.execute("SELECT * FROM live_deployments ORDER BY deployment_id")
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor]
    (temporary / "live_deployments.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (temporary / "legacy_schema.sql").write_text(
        "\n".join(
            row[0] + ";"
            for row in connection.execute("SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY type, name")
        )
        + f"\nPRAGMA user_version = {report.source_schema_version};\n",
        encoding="utf-8",
    )
    hashes = {
        name: _hash_file(temporary / name)
        for name in ("portfolio.db.bak", "live_deployments.jsonl", "legacy_schema.sql")
    }
    manifest = {
        "migration": f"portfolio-v{report.source_schema_version}-to-v6",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "database_filename": Path(report.database_path).name,
        **{
            key: value
            for key, value in asdict(report).items()
            if key.endswith("_count")
            or key in ("source_schema_version", "target_schema_version", "live_deployment_status_counts")
        },
        "artifacts": {name: f"sha256:{digest}" for name, digest in hashes.items()},
    }
    (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    hashes["manifest.json"] = _hash_file(temporary / "manifest.json")
    (temporary / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())), encoding="utf-8"
    )
    for artifact in temporary.iterdir():
        artifact.chmod(0o600)
        with artifact.open("rb") as file:
            os.fsync(file.fileno())
    temporary.rename(final)


def migrate_portfolio(db_path: str | Path, *, dry_run: bool = False) -> MigrationReport:
    """Back up and migrate an existing DB; dry-run never creates an archive."""
    path = Path(db_path).expanduser().resolve()
    if dry_run:
        # Even mode=ro may create/modify WAL sidecars. Refuse before opening
        # SQLite rather than use immutable=1, which can hide committed WAL data.
        with path.open("rb") as file:
            header = file.read(20)
        if header[18:20] == b"\x02\x02" or Path(str(path) + "-wal").exists():
            raise PortfolioSchemaError(
                "WAL dry-run cannot guarantee zero file writes. Stop all writers and use the old version "
                "to checkpoint and switch this database to journal_mode=DELETE before dry-run."
            )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)) as source:
        source.execute("BEGIN")
        version = schema_version(source)
        validate_schema(source, version)
        report = _report(source, path, version)
        if dry_run or version == 6:
            return report
        if not report.safe:
            raise LegacyLiveDeploymentActiveError(report.reason)
        fingerprint = _fingerprint(source)
        _write_archive(source, report)
        source.rollback()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if _fingerprint(connection) != fingerprint:
                raise PortfolioSchemaError(
                    "Portfolio database changed after backup; rerun migration with writers stopped"
                )
            validate_schema(connection, version)
            upgrade_schema(connection, version)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return report
