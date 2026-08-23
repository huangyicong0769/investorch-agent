from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from qmt_agent.config import AppConfig, ConfigError
from qmt_agent.storage import init_session_metadata

LOCAL_CONFIG_TEMPLATE = """# Local QMT Agent configuration.
# Overrides config/qmt.toml and stores local secrets.

[secrets]
"""

MCP_CONFIG_TEMPLATE = """# Local MCP server configuration.
"""


@dataclass(frozen=True)
class BootstrapSyncResult:
    created: int
    updated: int
    unchanged: int
    backup_dir: Path | None


@dataclass(frozen=True)
class _BootstrapTarget:
    target: Path
    template: str
    template_bytes: bytes
    existed: bool
    original: bytes | None


BootstrapMergeCallback = Callable[[Path, str, bool], Awaitable[None]]


def initialize(config: AppConfig, *, copy_bootstrap: bool = True) -> bool:
    """
    Initialize persistent local QMT Agent state.

    Returns True when root/qmt.toml is created, so the caller can ask the user to configure required secrets before continuing.
    """
    _ensure_directory(config.root, name="paths.root")

    root_config_created = _ensure_file(
        config.root_config_path,
        LOCAL_CONFIG_TEMPLATE,
        private=True,
    )

    _ensure_file(config.mcp_config_path, MCP_CONFIG_TEMPLATE, private=True)

    _ensure_directory(config.workspace_dir, name="workspace")

    _ensure_directory(config.state_dir, name="state")

    # Session schema initialization is idempotent.
    init_session_metadata(config.sessions_db)

    if copy_bootstrap:
        _copy_bootstrap_files(config)

    return root_config_created


def _copy_bootstrap_files(config: AppConfig) -> None:
    for source, target in config.bootstrap_files:
        # Existing workspace state always belongs
        # to the user and must never be overwritten.
        if target.exists():
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        target.write_bytes(source.read_bytes())


async def sync_bootstrap_files(config: AppConfig, merge: BootstrapMergeCallback) -> BootstrapSyncResult:
    """Synchronize bootstrap templates through a restricted merge Agent callback."""
    entries = _load_bootstrap_targets(config)
    backup_dir: Path | None = None
    created = 0
    updated = 0
    unchanged = 0

    try:
        for entry in entries:
            if entry.existed and entry.original == entry.template_bytes:
                unchanged += 1
                continue

            if entry.existed and backup_dir is None:
                backup_dir = _create_backup_dir(config.state_dir)

            backup_path = _backup_target(config.workspace_dir, backup_dir, entry)

            try:
                await merge(entry.target, entry.template, entry.existed)
                _validate_bootstrap_target(entry.target)
                current = entry.target.read_bytes()
            except Exception as exc:
                try:
                    _restore_bootstrap_target(entry, backup_path)
                except Exception as restore_exc:
                    raise ConfigError(
                        f"Bootstrap sync failed for {entry.target}; restore failed; backup directory: {backup_dir or 'none'}: {restore_exc}"
                    ) from restore_exc

                raise ConfigError(
                    f"Bootstrap sync failed for {entry.target}; backup directory: {backup_dir or 'none'}: {exc}"
                ) from exc

            except BaseException:
                _restore_bootstrap_target(entry, backup_path)
                raise

            if entry.existed:
                if current == entry.original:
                    unchanged += 1
                    if backup_path:
                        _remove_backup(backup_path, backup_dir)
                else:
                    updated += 1
            else:
                created += 1

        if updated == 0 and backup_dir and _remove_empty_backup_dir(backup_dir):
            backup_dir = None

        return BootstrapSyncResult(
            created=created,
            updated=updated,
            unchanged=unchanged,
            backup_dir=backup_dir,
        )
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"Bootstrap sync failed; backup directory: {backup_dir}: {exc}") from exc


def _load_bootstrap_targets(config: AppConfig) -> list[_BootstrapTarget]:
    entries: list[_BootstrapTarget] = []

    for source, target in config.bootstrap_files:
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ConfigError(f"Bootstrap target is not a regular file: {target}")

        try:
            template_bytes = source.read_bytes()
            template = template_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError(f"Bootstrap template is not valid UTF-8: {source}") from exc

        original = target.read_bytes() if target.exists() else None
        entries.append(_BootstrapTarget(target, template, template_bytes, original is not None, original))

    return entries


def _backup_target(workspace: Path, backup_dir: Path | None, entry: _BootstrapTarget) -> Path | None:
    if not entry.existed or backup_dir is None:
        return None

    backup_path = backup_dir / entry.target.relative_to(workspace)

    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry.target, backup_path)
    except OSError as exc:
        raise ConfigError(f"Unable to back up bootstrap target: {entry.target}; backup directory: {backup_dir}") from exc

    return backup_path


def _remove_backup(path: Path, backup_dir: Path | None) -> None:
    path.unlink(missing_ok=True)

    if backup_dir is None:
        return

    parent = path.parent

    while parent != backup_dir:
        try:
            parent.rmdir()
        except OSError:
            break

        parent = parent.parent


def _remove_empty_backup_dir(backup_dir: Path) -> bool:
    try:
        backup_dir.rmdir()
    except OSError:
        return False

    return True


def _validate_bootstrap_target(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"Bootstrap target is not a regular file: {path}")

    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Bootstrap target is not valid UTF-8: {path}") from exc


def _restore_bootstrap_target(entry: _BootstrapTarget, backup_path: Path | None) -> None:
    if entry.existed and backup_path:
        _atomic_write(entry.target, backup_path.read_bytes())
        return

    if entry.target.is_file() or entry.target.is_symlink():
        entry.target.unlink()


def _create_backup_dir(state_dir: Path) -> Path:
    backup_root = state_dir / "bootstrap-backups"

    try:
        backup_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"Unable to create bootstrap backup directory: {backup_root}") from exc

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backup_root / timestamp
    suffix = 1

    while True:
        try:
            backup_dir.mkdir()
            return backup_dir
        except FileExistsError:
            backup_dir = backup_root / f"{timestamp}-{suffix}"
            suffix += 1
        except OSError as exc:
            raise ConfigError(f"Unable to create bootstrap backup directory: {backup_dir}") from exc


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)

    try:
        with os.fdopen(file_descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _ensure_directory(path: Path, *, name: str) -> None:
    if path.exists():
        if not path.is_dir():
            raise ConfigError(f"{name} is not a directory: {path}")

        return

    path.mkdir(parents=True, exist_ok=True)


def _ensure_file(
    path: Path,
    content: str,
    *,
    private: bool = False,
) -> bool:
    if path.exists():
        if not path.is_file():
            raise ConfigError(f"Expected a file: {path}")

        return False

    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding="utf-8")

    if private and os.name == "posix":
        path.chmod(0o600)

    return True
