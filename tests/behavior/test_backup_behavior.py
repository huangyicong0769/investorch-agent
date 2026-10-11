import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import zipfile
from contextlib import closing

import pytest

from investorch.backup import BackupError, create_backup, verify_backup
from investorch.config import load_config
from investorch.initializer import initialize
from investorch.skills.registry import read_registry


def populated_root(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "investorch.toml").write_text(
        'config_version = 1\n[paths]\nstate = "records"\nworkspace = "research"\n'
        '[secrets]\nprovider = "secret-sentinel"\n'
    )
    config = load_config(root, migrate=False)
    initialize(config)
    (config.mcp_config_path).write_text('[servers.private]\nurl = "https://example.org?token=mcp-sentinel"\n')
    (config.workspace_dir / "strategy.py").write_text("print('research')\n")
    (config.session_journal_dir / "session-a.jsonl").write_text('{"type":"user_message","text":"local note"}\n')
    with closing(sqlite3.connect(config.sessions_db)) as db:
        db.execute("CREATE TABLE recovery_probe (id TEXT PRIMARY KEY, note TEXT)")
        db.execute("INSERT INTO recovery_probe VALUES ('session-a', 'preserved')")
        db.commit()
    return config


def test_backup_restores_actual_custom_layout_and_logical_state(tmp_path):
    config = populated_root(tmp_path)
    output = tmp_path / "backup.zip"
    created = create_backup(config, output)
    manifest = verify_backup(output)
    assert created["path"] == str(output)
    assert manifest["layout"]["workspace"] == "research"
    assert manifest["layout"]["state"] == "records"
    assert manifest["sensitive_config_included"] is False
    target = tmp_path / "restored"
    target.mkdir()
    with zipfile.ZipFile(output) as archive:
        for entry in manifest["files"]:
            destination = target / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(entry["path"]))
    restored = load_config(target, migrate=False)
    with closing(sqlite3.connect(restored.sessions_db)) as db:
        assert db.execute("SELECT * FROM recovery_probe").fetchall() == [("session-a", "preserved")]
    assert (restored.session_journal_dir / "session-a.jsonl").read_bytes() == (
        config.session_journal_dir / "session-a.jsonl"
    ).read_bytes()
    assert read_registry(restored.state_dir / "skills.json") == read_registry(config.state_dir / "skills.json")
    assert (restored.workspace_dir / "strategy.py").read_bytes() == (config.workspace_dir / "strategy.py").read_bytes()
    assert restored.portfolio_db.exists()
    assert restored.secrets == {}
    assert not restored.mcp_config_path.exists()
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600


def test_backup_sensitive_and_artifact_exclusions_are_explicit(tmp_path):
    config = populated_root(tmp_path)
    config.backtest_artifact_dir.mkdir(parents=True)
    (config.backtest_artifact_dir / "result.txt").write_text("artifact")
    config.background_job_dir.mkdir(parents=True)
    (config.background_job_dir / "private.log").write_text("transient sentinel")
    (config.log_dir / "private.log").write_text("log sentinel")
    plain = tmp_path / "plain.zip"
    create_backup(config, plain, exclude_artifacts=True)
    with zipfile.ZipFile(plain) as archive:
        config_bytes = archive.read("investorch.toml")
        assert "secrets" not in tomllib.loads(config_bytes.decode())
        assert b"secret-sentinel" not in config_bytes
        assert "mcp.toml" not in archive.namelist()
        assert not any("result.txt" in name or "private.log" in name for name in archive.namelist())
    sensitive = tmp_path / "sensitive.zip"
    create_backup(config, sensitive, include_sensitive_config=True)
    assert verify_backup(sensitive)["sensitive_config_included"] is True
    with zipfile.ZipFile(sensitive) as archive:
        assert b"secret-sentinel" in archive.read("investorch.toml")
        assert b"mcp-sentinel" in archive.read("mcp.toml")
        assert any(name.endswith("result.txt") for name in archive.namelist())


def test_backup_preserves_committed_wal_rows_without_checkpointing_source(tmp_path):
    config = populated_root(tmp_path)
    with closing(sqlite3.connect(config.sessions_db)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO recovery_probe VALUES ('wal-row', 'committed')")
        writer.commit()
        paths = [config.sessions_db, config.sessions_db.with_name("sessions.db-wal")]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        output = tmp_path / "wal.zip"
        create_backup(config, output)
        assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        snapshot = tmp_path / "snapshot.db"
        with zipfile.ZipFile(output) as archive:
            snapshot.write_bytes(archive.read("records/sessions.db"))
        with closing(sqlite3.connect(snapshot)) as db:
            assert db.execute("SELECT note FROM recovery_probe WHERE id='wal-row'").fetchone() == ("committed",)


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "modified", "traversal", "manifest"])
def test_verify_rejects_archive_damage_without_configuration(tmp_path, damage):
    config = populated_root(tmp_path)
    original = tmp_path / "original.zip"
    create_backup(config, original)
    damaged = tmp_path / "damaged.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(damaged, "w") as destination:
        for name in source.namelist():
            data = source.read(name)
            if name == "research/strategy.py":
                if damage == "missing":
                    continue
                if damage == "modified":
                    data += b"modified"
            if name == "manifest.json" and damage in ("traversal", "manifest"):
                manifest = json.loads(data)
                if damage == "traversal":
                    manifest["files"][0]["path"] = "../outside"
                else:
                    manifest["format_version"] = 999
                data = json.dumps(manifest).encode()
            destination.writestr(name, data)
        if damage == "extra":
            destination.writestr("extra.txt", "unlisted")
        elif damage == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                destination.writestr("research/strategy.py", "duplicate")
    with pytest.raises(BackupError):
        verify_backup(damaged)


def test_backup_rejects_existing_and_recursive_outputs_and_skips_symlinks(tmp_path):
    config = populated_root(tmp_path)
    outside = tmp_path / "private.txt"
    outside.write_text("outside sentinel")
    (config.workspace_dir / "link.txt").symlink_to(outside)
    with pytest.raises(BackupError):
        create_backup(config, config.workspace_dir / "recursive.zip")
    with pytest.raises(BackupError):
        create_backup(config, outside)
    output = tmp_path / "safe.zip"
    result = create_backup(config, output)
    assert any(entry["path"] == "research/link.txt" for entry in result["manifest"]["excluded"])
    with zipfile.ZipFile(output) as archive:
        assert "research/link.txt" not in archive.namelist()


@pytest.mark.skipif(shutil.which("lsof") is None, reason="Platform does not provide open-file inspection")
def test_backup_refuses_an_external_workspace_writer(tmp_path):
    config = populated_root(tmp_path)
    script = "import sys; file = open(sys.argv[1], 'w'); print('ready', flush=True); sys.stdin.read()"
    with subprocess.Popen(
        [sys.executable, "-c", script, str(config.workspace_dir / "active.txt")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        assert child.stdout.readline().strip() == "ready"
        try:
            with pytest.raises(BackupError, match="stop writers"):
                create_backup(config, tmp_path / "active.zip")
        finally:
            child.communicate(timeout=5)


def test_failed_database_backup_leaves_no_final_or_incomplete_archive(tmp_path):
    config = populated_root(tmp_path)
    config.portfolio_db.write_bytes(b"broken database")
    output = tmp_path / "failed.zip"
    with pytest.raises(BackupError, match="DatabaseError"):
        create_backup(config, output)
    assert config.portfolio_db.read_bytes() == b"broken database"
    assert not output.exists()
    assert not list(tmp_path.glob(".failed.zip.incomplete-*"))
