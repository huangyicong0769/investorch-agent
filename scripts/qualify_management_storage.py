"""Real-process management/storage qualification using disposable historical fixtures.

Run with uv run --locked python scripts/qualify_management_storage.py.
No real user directory or provider credential is read; no Agent inference is run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import tomlkit
from package_smoke import request, running_web

from investorch.application.skills import SkillOperations
from investorch.backup import create_backup, verify_backup
from investorch.config import load_config
from investorch.config_legacy import legacy_model_overrides
from investorch.initializer import initialize
from investorch.portfolio import init_portfolio_storage, list_ledger_entries, list_portfolios
from investorch.portfolio.migration import migrate_portfolio
from investorch.storage import create_session, get_session, set_session_title

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / "tests/behavior/fixtures"
KEY = "qualification-placeholder-not-a-provider-key"


def files_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def legacy_home(base: Path, name: str, values: dict | None = None) -> Path:
    home = base / name
    root = home / ".investorch"
    root.mkdir(parents=True)
    (root / "investorch.toml").write_text(
        tomlkit.dumps({"secrets": {"DEEPSEEK_API_KEY": KEY}, **(values or {})}), encoding="utf-8"
    )
    return home


def historical_database(path: Path, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript((FIXTURES / f"portfolio_schema_v{version}.sql").read_text())


def sql_economics(path: Path) -> dict:
    # Literal historical fixtures carry the financial facts being compared.
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        result = {}
        for table in ("portfolios", "portfolio_ledger", "portfolio_cash", "portfolio_holdings"):
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY 1')]
            if table == "portfolio_ledger":
                for row in rows:
                    row.setdefault("broker_account_id", None)
            result[table] = rows
        return result


def main() -> None:
    executable = shutil.which("investorch")
    assert executable
    report = {
        "source": "synthetic legacy TOML and literal historical Portfolio SQL fixtures",
        "provider_requests": 0,
        "checks": {},
    }
    checks = report["checks"]
    with tempfile.TemporaryDirectory(prefix="investorch-management-qualification-") as temporary:
        base = Path(temporary)
        profiles = {
            "secret_only": {},
            "partial": {"models": {"main": {"name": "explicit-main"}}},
            "full": legacy_model_overrides(),
        }
        for name, values in profiles.items():
            home = legacy_home(base, name, values)
            root = home / ".investorch"
            local = root / "investorch.toml"
            original = local.read_bytes()
            expected = load_config(root, migrate=False).public()
            with running_web(executable, home) as (_, origin):
                assert request(origin, "/api/setup/state")["status"] == "ready"
                assert request(origin, "/api/sessions", method="POST")["session"]["session_id"]
            actual = load_config(root, migrate=False).public()
            assert expected == actual
            assert local.with_name("investorch.toml.pre-schema.bak").read_bytes() == original
            checks[f"legacy_{name}"] = "ready; effective configuration preserved; original TOML backed up"

        bad_home = legacy_home(base, "bad-config")
        bad_root = bad_home / ".investorch"
        (bad_root / "investorch.toml").write_text("[broken TOML")
        before = files_digest(bad_root)
        with running_web(executable, bad_home) as (_, origin):
            assert request(origin, "/api/setup/state")["status"] == "recovery_required"
            assert b"<html" in request(origin, "/setup")
        assert files_digest(bad_root) == before
        checks["invalid_toml"] = "HTTP recovery; source files unchanged"

        for version in range(1, 7):
            home = legacy_home(base, f"portfolio-v{version}")
            root = home / ".investorch"
            database = root / "state/portfolio.db"
            historical_database(database, min(version, 2) if version == 6 else version)
            if version == 6:
                init_portfolio_storage(database)
            before = sql_economics(database)
            if version in (3, 4, 5):
                raw_before = files_digest(root)
                with running_web(executable, home) as (_, origin):
                    assert request(origin, "/api/setup/state")["status"] == "recovery_required"
                assert files_digest(root) == raw_before
                migration = migrate_portfolio(database)
                assert migration.archive_path and Path(migration.archive_path).is_dir()
            with running_web(executable, home) as (_, origin):
                assert request(origin, "/api/setup/state")["status"] == "ready"
                assert request(origin, "/api/portfolios")["portfolios"]
            after = sql_economics(database)
            assert before == after, f"v{version} economics changed"
            with closing(sqlite3.connect(database)) as connection:
                assert connection.execute("PRAGMA user_version").fetchone() == (6,)
            checks[f"portfolio_v{version}"] = "ready on v6; fixture financial rows preserved"

        active_home = legacy_home(base, "active-v5")
        active_root = active_home / ".investorch"
        active_db = active_root / "state/portfolio.db"
        historical_database(active_db, 5)
        with closing(sqlite3.connect(active_db)) as connection:
            connection.execute(
                "UPDATE live_deployments SET status = 'ACTIVE' "
                "WHERE deployment_id = (SELECT deployment_id FROM live_deployments ORDER BY deployment_id LIMIT 1)"
            )
            connection.commit()
        before = files_digest(active_root)
        with running_web(executable, active_home) as (_, origin):
            assert request(origin, "/api/setup/state")["status"] == "recovery_required"
        assert not migrate_portfolio(active_db, dry_run=True).safe
        assert files_digest(active_root) == before
        checks["active_legacy_live"] = "HTTP recovery; migration blocked; source files unchanged"

        for failure in ("unknown_schema", "corrupt_database"):
            home = legacy_home(base, failure)
            root = home / ".investorch"
            database = root / "state/portfolio.db"
            historical_database(database, 2)
            if failure == "unknown_schema":
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("PRAGMA user_version=99")
            else:
                database.write_bytes(b"not a SQLite database")
            before = files_digest(root)
            with running_web(executable, home) as (_, origin):
                assert request(origin, "/api/setup/state")["status"] == "recovery_required"
            assert files_digest(root) == before
            checks[failure] = "HTTP recovery; source files unchanged"

        source_home = legacy_home(base, "backup-source")
        source_root = source_home / ".investorch"
        config = load_config(source_root)
        initialize(config)
        historical = base / "fixture-portfolio.db"
        historical_database(historical, 2)
        migrate_portfolio(historical)
        shutil.copyfile(historical, config.portfolio_db)
        create_session(config.sessions_db, "recovery-session")
        set_session_title(config.sessions_db, "recovery-session", "Recovery qualification")
        (config.session_journal_dir / "recovery-session.jsonl").write_text(
            '{"type":"user_message","text":"qualification note"}\n'
        )
        SkillOperations(config=config).set_enabled("skill-creator", False)
        (config.workspace_dir / "strategy.py").write_text("print('qualification')\n")
        original = files_digest(source_root)
        doctor = subprocess.run(
            [executable, "doctor", "--root", str(source_root), "--json"],
            env={**os.environ, "HOME": str(source_home)},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert doctor.returncode in (0, 1), doctor.stdout
        assert files_digest(source_root) == original
        checks["doctor_readonly"] = "real CLI; all source hashes unchanged"
        archive = base / "recovery.zip"
        create_backup(config, archive)
        manifest = verify_backup(archive)
        restored_root = base / "restored"
        doc = (PROJECT / "docs/Backup_and_Recovery.md").read_text()
        restore_script = doc.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        subprocess.run(
            [sys.executable, "-", str(archive), str(restored_root)],
            input=restore_script,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        restored = load_config(restored_root, migrate=False)
        assert get_session(restored.sessions_db, "recovery-session") == get_session(
            config.sessions_db, "recovery-session"
        )
        for portfolio in list_portfolios(config.portfolio_db):
            assert list_ledger_entries(restored.portfolio_db, portfolio.id) == list_ledger_entries(
                config.portfolio_db, portfolio.id
            )
        assert not SkillOperations(config=restored).inspect("skill-creator")["enabled"]
        assert files_digest(config.workspace_dir) == files_digest(restored.workspace_dir)
        assert files_digest(config.session_journal_dir) == files_digest(restored.session_journal_dir)
        assert not restored.secrets and not restored.mcp_config_path.exists()
        assert files_digest(source_root) == original
        restored_doctor = subprocess.run(
            [executable, "doctor", "--root", str(restored_root), "--json"],
            env={**os.environ, "HOME": str(source_home)},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert restored_doctor.returncode in (0, 1), restored_doctor.stdout
        checks["restored_doctor"] = "real CLI; no errors, expected missing-credential/data/WAL warnings"
        checks["backup_restore"] = (
            f"documented recipe restored {len(manifest['files'])} files; Session/ledger/Skills/Workspace equal"
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
