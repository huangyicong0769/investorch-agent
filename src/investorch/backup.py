"""Offline, private archives of local user state; no application startup or restore."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import tomlkit

from investorch.config import CONFIG_VERSION, AppConfig


class BackupError(ValueError):
    pass


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise BackupError("Backup source escapes the configured root") from None


def _safe_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or "\0" in value
        or PurePosixPath(value).is_absolute()
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise BackupError(f"Unsafe archive path: {value!r}")
    return value


def _offline_preflight(root: Path) -> list[str]:
    warnings = [
        "This archive is unencrypted; Workspace and Journal content may contain private data or credentials.",
        "Consistency requires all InvestOrch clients and other writers to remain stopped throughout backup.",
    ]
    executable = shutil.which("lsof")
    if executable is None:
        warnings.append("Open-file checks are unavailable on this platform; confirm that all writers are stopped.")
        return warnings
    try:
        result = subprocess.run(
            [executable, "-nP", "-F", "pfan", "+D", str(root)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError("Cannot check active writers; stop writers and retry the offline backup") from exc
    if result.returncode not in (0, 1):
        raise BackupError("Open-file inspection failed; cannot establish the offline backup precondition")
    process = None
    writable = False
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            process = line[1:]
        elif line.startswith("f"):
            writable = False
        elif line.startswith("a"):
            writable = line[1:] in ("w", "u")
        elif line.startswith("n") and writable and process != str(os.getpid()):
            raise BackupError(f"Process {process} has a writable file open under the root; stop writers before backup")
    if result.stderr.strip():
        warnings.append(
            "Open-file inspection reported incomplete visibility; independently confirm all writers are stopped."
        )
    return warnings


def _file_state(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _snapshot_database(path: Path, scratch: Path) -> tuple[Path, int]:
    # Copy the offline DB/WAL pair first: SQLite mode=ro can still create SHM.
    # All SQLite side effects therefore stay in private scratch, never user state.
    directory = scratch / path.stem
    directory.mkdir()
    source_path = directory / "source.db"
    sources = [path]
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        if wal.is_symlink():
            raise BackupError("SQLite WAL must not be a symlink")
        sources.append(wal)
    before = {source: _file_state(source) for source in sources}
    shutil.copyfile(path, source_path)
    if wal in before:
        shutil.copyfile(wal, directory / "source.db-wal")
    if before != {source: _file_state(source) for source in sources} or wal.exists() != (wal in before):
        raise BackupError("SQLite state changed during backup; stop all writers and retry")
    snapshot = directory / "snapshot.db"
    with (
        closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(snapshot)) as destination,
    ):
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError(f"SQLite integrity check failed: {path.name}")
        schema = destination.execute("PRAGMA user_version").fetchone()[0]
    return snapshot, schema


def _safe_config(config: AppConfig) -> bytes:
    effective = config.public(saved=True)
    local: dict = {"config_version": CONFIG_VERSION}
    for key in config.override_keys():
        parts = key.split(".")
        if parts[0] == "secrets" or key == "paths.root":
            continue
        value = effective
        for part in parts:
            value = value[part]
        if value is None:
            continue
        if key.endswith(".base_url") and isinstance(value, str):
            try:
                url = urlsplit(value)
                if url.username or url.password or url.query or url.fragment:
                    continue
            except ValueError:
                continue
        target = local
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return tomlkit.dumps(local).encode("utf-8")


def _write_entry(archive: zipfile.ZipFile, path: Path, name: str) -> dict:
    _safe_path(name)
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    digest = hashlib.sha256()
    size = 0
    before = _file_state(path)
    with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as destination:
        while chunk := source.read(1024 * 1024):
            destination.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if before != _file_state(path):
        raise BackupError(f"Source changed during backup: {name}")
    return dict(path=name, size=size, sha256=digest.hexdigest())


def create_backup(
    config: AppConfig,
    output: Path,
    *,
    include_sensitive_config: bool = False,
    exclude_artifacts: bool = False,
) -> dict:
    root = config.root.resolve()
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise BackupError("Backup output already exists; choose a new path")
    if output.resolve().is_relative_to(root):
        raise BackupError("Backup output must be outside the configured root")
    if not root.is_dir():
        raise BackupError("Backup root does not exist")
    if not output.parent.is_dir():
        raise BackupError("Backup output directory does not exist")
    warnings = _offline_preflight(root)
    if include_sensitive_config:
        warnings.append("Raw configuration credentials are included in this unencrypted local archive.")
    excluded = [
        dict(path=_relative(config.log_dir, root), reason="logs"),
        dict(path=_relative(config.background_job_dir, root), reason="transient_jobs"),
        dict(path=_relative(config.rqalpha_bundle_dir, root), reason="downloaded_market_data"),
        dict(path=_relative(config.cnequity_config_path, root), reason="optional_data_configuration"),
    ]
    if not include_sensitive_config:
        excluded.extend(
            [dict(path="investorch.toml:secrets", reason="raw_secrets"), dict(path="mcp.toml", reason="raw_mcp_config")]
        )
    excluded_directories = [config.background_job_dir, config.rqalpha_bundle_dir, config.log_dir]
    if exclude_artifacts:
        excluded_directories.append(config.backtest_artifact_dir)
        excluded.append(dict(path=_relative(config.backtest_artifact_dir, root), reason="artifacts_requested"))
    manifest = dict(
        format_version=1,
        application_version=version("investorch"),
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        sensitive_config_included=include_sensitive_config,
        artifacts_excluded=exclude_artifacts,
        layout=dict(
            state=_relative(config.state_dir, root),
            workspace=_relative(config.workspace_dir, root),
            sessions_db=_relative(config.sessions_db, root),
            portfolio_db=_relative(config.portfolio_db, root),
            journals=_relative(config.session_journal_dir, root),
        ),
        warnings=warnings,
        excluded=excluded,
        files=[],
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.incomplete-", dir=output.parent)
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        with tempfile.TemporaryDirectory(prefix="investorch-backup-") as scratch_name:
            scratch = Path(scratch_name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                included: set[str] = set()

                def add(path: Path, *, snapshot: bool = False) -> None:
                    name = _relative(path, root)
                    if path.is_symlink():
                        excluded.append(dict(path=name, reason="symlink_not_followed"))
                        return
                    if not path.exists():
                        excluded.append(dict(path=name, reason="not_present"))
                        return
                    if name in included:
                        return
                    source = path
                    schema = None
                    if snapshot:
                        source, schema = _snapshot_database(path, scratch)
                    entry = _write_entry(archive, source, name)
                    if schema is not None:
                        entry["sqlite_schema_version"] = schema
                    manifest["files"].append(entry)
                    included.add(name)

                def tree(directory: Path) -> None:
                    if directory.is_symlink():
                        excluded.append(dict(path=_relative(directory, root), reason="symlink_not_followed"))
                        return
                    if not directory.exists():
                        return
                    if any(directory == skip or directory.is_relative_to(skip) for skip in excluded_directories):
                        return
                    for current, dirs, files in os.walk(directory, followlinks=False):
                        parent = Path(current)
                        for child in list(dirs):
                            path = parent / child
                            if path.is_symlink() or any(path == skip for skip in excluded_directories):
                                dirs.remove(child)
                                if path.is_symlink():
                                    excluded.append(dict(path=_relative(path, root), reason="symlink_not_followed"))
                        for name in sorted(files):
                            path = parent / name
                            if path == config.cnequity_config_path:
                                continue
                            add(path)

                for db in (config.sessions_db, config.portfolio_db):
                    add(db, snapshot=True)
                add(config.state_dir / "skills.json")
                tree(config.session_journal_dir)
                tree(config.workspace_dir)
                if include_sensitive_config:
                    add(config.root_config_path)
                    add(config.mcp_config_path)
                else:
                    safe = scratch / "investorch.toml"
                    safe.write_bytes(_safe_config(config))
                    manifest["files"].append(_write_entry(archive, safe, "investorch.toml"))
                archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            verify_backup(temporary)
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        # Linking publishes atomically and refuses a concurrently created target.
        os.link(temporary, output)
        temporary.unlink()
        return dict(path=str(output), manifest=manifest)
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise BackupError(f"Backup creation failed: {type(exc).__name__}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def verify_backup(path: Path) -> dict:
    """Verify archive metadata and every streamed member without opening user state."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise BackupError("Archive contains duplicate entry names")
            for name in names:
                _safe_path(name)
            if "manifest.json" not in names:
                raise BackupError("Archive is missing manifest.json")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                not isinstance(manifest, dict)
                or type(manifest.get("format_version")) is not int
                or manifest["format_version"] != 1
            ):
                raise BackupError("Unsupported or invalid backup manifest")
            if any(type(manifest.get(key)) is not bool for key in ("sensitive_config_included", "artifacts_excluded")):
                raise BackupError("Manifest configuration and artifact flags must be booleans")
            layout = manifest.get("layout")
            if not isinstance(layout, dict) or set(layout) != {
                "state",
                "workspace",
                "sessions_db",
                "portfolio_db",
                "journals",
            }:
                raise BackupError("Manifest recovery layout is invalid")
            for location in layout.values():
                _safe_path(location)
            records = manifest.get("files")
            if not isinstance(records, list):
                raise BackupError("Manifest files must be a list")
            expected = {"manifest.json"}
            for record in records:
                if not isinstance(record, dict):
                    raise BackupError("Manifest file record is invalid")
                name = _safe_path(record.get("path"))
                if name in expected:
                    raise BackupError(f"Manifest contains duplicate path: {name}")
                expected.add(name)
                if name not in names:
                    raise BackupError(f"Archive is missing file: {name}")
                if type(record.get("size")) is not int or record["size"] < 0:
                    raise BackupError(f"Invalid file size in manifest: {name}")
                digest = hashlib.sha256()
                size = 0
                header = b""
                with archive.open(name) as source:
                    while chunk := source.read(1024 * 1024):
                        if not header:
                            header = chunk[:100]
                        digest.update(chunk)
                        size += len(chunk)
                if size != record["size"] or digest.hexdigest() != record.get("sha256"):
                    raise BackupError(f"Archive size or SHA-256 mismatch: {name}")
                if "sqlite_schema_version" in record and (
                    type(record["sqlite_schema_version"]) is not int
                    or len(header) < 100
                    or header[:16] != b"SQLite format 3\0"
                    or int.from_bytes(header[60:64], "big") != record["sqlite_schema_version"]
                ):
                    raise BackupError(f"SQLite schema version does not match manifest: {name}")
            if set(names) != expected:
                raise BackupError("Archive contains files not listed in its manifest")
            return manifest
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as exc:
        raise BackupError(f"Backup verification failed: {type(exc).__name__}: {exc}") from exc
