from __future__ import annotations

import json
from pathlib import Path

from investorch.diagnostics import doctor_checks, doctor_exit_code


def test_doctor_reports_missing_installation_without_creating_it(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    checks = {item["code"]: item for item in doctor_checks(root)}
    assert not root.exists()
    assert checks["config"]["status"] == "warning"
    assert checks["credentials"]["status"] == "warning"
    assert checks["sessions"]["status"] == "warning"
    assert checks["portfolio_schema"]["status"] == "warning"
    assert checks["cnequity"]["status"] == "disabled"
    assert doctor_exit_code(list(checks.values())) == 1
    assert all(item["checked_at"] for item in checks.values())


def test_doctor_reports_bad_config_without_echoing_or_changing_it(tmp_path: Path) -> None:
    path = tmp_path / "investorch.toml"
    original = b'[secrets]\nTOP_SECRET_NAME = "secret-value"\ninvalid = [\n'
    path.write_bytes(original)
    before = set(tmp_path.rglob("*"))
    checks = doctor_checks(tmp_path)
    assert next(item for item in checks if item["code"] == "config")["status"] == "error"
    assert doctor_exit_code(checks) == 2
    assert "secret-value" not in json.dumps(checks)
    assert "TOP_SECRET_NAME" not in json.dumps(checks)
    assert path.read_bytes() == original
    assert set(tmp_path.rglob("*")) == before


def test_missing_legacy_credentials_are_setup_warning_not_config_failure(tmp_path: Path) -> None:
    path = tmp_path / "investorch.toml"
    path.write_text('[secrets]\nUNUSED_NAME = "secret-value"\n')
    original = path.read_bytes()
    checks = {item["code"]: item for item in doctor_checks(tmp_path, upgrade=True)}
    assert checks["config"]["status"] == "warning"
    assert checks["credentials"]["configured"]["main"] is False
    assert checks["config_upgrade"]["status"] == "warning"
    assert "UNUSED_NAME" not in json.dumps(checks)
    assert "secret-value" not in json.dumps(checks)
    assert path.read_bytes() == original


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_offline_doctor_checks_real_session_and_portfolio_without_writes(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    from investorch.config import load_config
    from investorch.portfolio import init_portfolio_storage
    from investorch.storage import init_session_metadata

    config = load_config(root=tmp_path, migrate=False)
    config.state_dir.mkdir()
    init_session_metadata(config.sessions_db)
    init_portfolio_storage(config.portfolio_db)
    # SQLiteSession uses WAL; explicitly prepare an offline DELETE-mode fixture.
    for path in (config.sessions_db, config.portfolio_db):
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
    before = _files(tmp_path)
    checks = {item["code"]: item for item in doctor_checks(tmp_path)}
    assert checks["sessions"]["status"] == "ok"
    assert checks["portfolio_schema"]["status"] == "ok"
    assert _files(tmp_path) == before


def test_doctor_does_not_touch_wal_or_corrupt_database(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    from investorch.config import load_config

    config = load_config(root=tmp_path, migrate=False)
    config.state_dir.mkdir()
    config.sessions_db.write_bytes(b"not SQLite data")
    with closing(sqlite3.connect(config.portfolio_db)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE committed_data(value)")
        connection.commit()
        before = _files(tmp_path)
        checks = {item["code"]: item for item in doctor_checks(tmp_path)}
        assert checks["sessions"]["status"] == "error"
        assert checks["portfolio_schema"]["status"] == "unknown"
        assert _files(tmp_path) == before


def test_upgrade_checks_historical_portfolios_without_migration(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    from investorch.config import load_config
    from investorch.storage import init_session_metadata
    from tests.support.portfolio_migration import load_legacy_portfolio_fixture

    for version in (1, 2, 3, 4, 5):
        root = tmp_path / str(version)
        config = load_config(root=root, migrate=False)
        config.state_dir.mkdir(parents=True)
        init_session_metadata(config.sessions_db)
        load_legacy_portfolio_fixture(config.portfolio_db, version)
        before = _files(root)
        checks = {item["code"]: item for item in doctor_checks(root, upgrade=True)}
        assert checks["portfolio_schema"]["source_schema_version"] == version
        assert checks["portfolio_schema"]["status"] == "warning"
        assert checks["portfolio_schema"]["safe"] is True
        assert _files(root) == before
        if version >= 3:
            with closing(sqlite3.connect(config.portfolio_db)) as connection:
                connection.execute(
                    "UPDATE live_deployments SET status='ACTIVE' WHERE rowid=(SELECT MIN(rowid) FROM live_deployments)"
                )
                connection.commit()
            before = _files(root)
            checks = {item["code"]: item for item in doctor_checks(root, upgrade=True)}
            assert checks["portfolio_schema"]["status"] == "error"
            assert checks["portfolio_schema"]["safe"] is False
            assert _files(root) == before


def test_doctor_finds_skill_content_drift_and_missing_files_without_updates(tmp_path: Path) -> None:
    import shutil

    from investorch.config import load_config
    from investorch.skills.bundled import update_builtins

    config = load_config(root=tmp_path, migrate=False)
    update_builtins(config)
    root = config.workspace_dir / "skills"
    names = sorted(path.name for path in root.iterdir())
    changed = root / names[0] / "SKILL.md"
    changed.write_text(changed.read_text() + "\nLocal edit without changing the version.\n")
    shutil.rmtree(root / names[1])
    (root / names[2] / "SKILL.md").write_text("Invalid frontmatter")
    before = _files(tmp_path)
    checks = {item["code"]: item for item in doctor_checks(tmp_path)}
    assert checks["skills"]["status"] == "warning"
    assert checks["skills"]["builtin_drift"] == [names[0]]
    assert checks["skills"]["missing"] == [names[1]]
    assert checks["skills"]["invalid"] == [names[2]]
    assert _files(tmp_path) == before


def test_doctor_keeps_mcp_tokens_and_names_out_of_reports(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text("""[[servers]]
name = "SECRET_SERVER_NAME"
url = "https://example.test/?token=secret-token"
transport = "streamable_http"
[servers.headers]
Authorization = "secret-header"
""")
    before = _files(tmp_path)
    checks = {item["code"]: item for item in doctor_checks(tmp_path)}
    assert checks["mcp"]["status"] == "unknown"
    assert checks["mcp"]["enabled"] == 1
    assert _files(tmp_path) == before
    for secret in ("SECRET_SERVER_NAME", "secret-token", "secret-header"):
        assert secret not in json.dumps(checks)
    path.write_text(path.read_text().replace('transport = "streamable_http"', 'transport = "secret-transport"'))
    checks = {item["code"]: item for item in doctor_checks(tmp_path)}
    assert checks["mcp"]["status"] == "error"
    assert "secret-transport" not in json.dumps(checks)


def test_doctor_reports_unreadable_bundle_without_aborting_other_checks(tmp_path: Path) -> None:
    from investorch.config import load_config

    config = load_config(root=tmp_path, migrate=False)
    config.rqalpha_bundle_dir.mkdir(parents=True)
    config.rqalpha_bundle_dir.chmod(0)
    try:
        checks = {item["code"]: item for item in doctor_checks(tmp_path)}
        assert checks["rqalpha"]["status"] == "error"
        assert checks["cnequity"]["status"] == "disabled"
    finally:
        config.rqalpha_bundle_dir.chmod(0o700)
