"""Offline diagnostics shared by CLI and Web, without application initialization."""

from __future__ import annotations

import importlib.util
import os
import platform
import sqlite3
import tomllib
from contextlib import closing
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from investorch.config import ConfigError, load_config
from investorch.mcp import read_mcp_server_configs
from investorch.portfolio.migration import migrate_portfolio
from investorch.portfolio.schema import PortfolioStorageError
from investorch.skills.bundled import BUNDLED_ROOT
from investorch.skills.registry import read_registry
from investorch.skills.validation import validate_skill


def doctor_exit_code(checks: list[dict[str, Any]]) -> int:
    """Return 0 for healthy/disabled, 1 for warning/unknown, and 2 for errors."""
    if any(check["status"] == "error" for check in checks):
        return 2
    return int(any(check["status"] in ("warning", "unknown") for check in checks))


def doctor_checks(root: Path | None = None, upgrade: bool = False) -> list[dict[str, Any]]:
    """Inspect local files without writes, network probes, or a running Agent."""
    root = (root or Path.home() / ".investorch").expanduser().resolve()
    checked_at = datetime.now(UTC).isoformat()
    checks: list[dict[str, Any]] = []

    def add(code: str, status: str, summary: str, detail: str = "", remediation: str = "", **metrics: Any) -> None:
        checks.append(
            dict(
                code=code,
                status=status,
                summary=summary,
                detail=detail,
                remediation=remediation,
                checked_at=checked_at,
                **metrics,
            )
        )

    try:
        package_version = version("investorch")
    except PackageNotFoundError:
        package_version = "unknown"
    add(
        "package",
        "ok" if package_version != "unknown" else "warning",
        f"InvestOrch {package_version}",
        f"Python {platform.python_version()}",
    )
    static = Path(__file__).parent / "web" / "static" / "index.html"
    add(
        "web_assets",
        "ok" if static.is_file() else "error",
        "WebUI assets available" if static.is_file() else "WebUI assets missing",
        remediation="Reinstall a complete InvestOrch package." if not static.is_file() else "",
    )
    config = None
    local = root / "investorch.toml"
    try:
        config = load_config(root=root, migrate=False)
        missing = config.readiness()
        add(
            "config",
            "warning" if missing else "ok",
            "Setup required" if missing else "Configuration is valid",
            "Required model settings are incomplete." if missing else "Schema validation passed.",
            "Run investorch web to complete Setup." if missing else "",
        )
        roles = ("main", "title", "activity", "permission", "compact")
        configured = {
            role: bool(config.secrets.get(config[f"models.{role}.api_key_secret"], "").strip())
            if config[f"models.{role}.api_key_secret"]
            else False
            for role in roles
        }
        add(
            "credentials",
            "ok" if all(configured.values()) else "warning",
            "Model credential presence",
            "Presence only; provider connectivity has not been tested.",
            configured=configured,
        )
        if upgrade:
            raw = tomllib.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
            legacy = local.exists() and "config_version" not in raw
            add(
                "config_upgrade",
                "warning" if legacy else "ok",
                "Legacy configuration compatibility required"
                if legacy
                else "Configuration format is current"
                if local.exists()
                else "New installation",
                "Historical model defaults are projected in memory only; explicit overrides are retained."
                if legacy
                else "",
                "Back up offline before normal startup writes the migrated configuration." if legacy else "",
                affected_settings=[f"models.{role}" for role in roles] if legacy else [],
            )
    except (ConfigError, OSError, ValueError, TypeError):
        # Validation errors can contain arbitrary user values (including credentials).
        add(
            "config",
            "error",
            "Configuration cannot be read or validated",
            "Local TOML or its values are invalid.",
            "Repair investorch.toml using the Settings schema or restore a known-good offline backup.",
        )
        add("credentials", "unknown", "Credential presence unavailable", "Repair configuration first.")

    if config is None:
        for code in ("sessions", "portfolio_schema", "skills", "mcp", "rqalpha", "cnequity", "filesystem"):
            add(
                code,
                "unknown",
                "Check skipped because configured paths are unavailable",
                remediation="Repair configuration and run investorch doctor again.",
            )
    else:
        _storage_checks(config.sessions_db, config.portfolio_db, add)
        _skill_checks(config.state_dir / "skills.json", config.workspace_dir / "skills", add)
        _integration_checks(config, add)
        inaccessible = []
        missing_paths = []
        for label, path in (
            ("root", root),
            ("workspace", config.workspace_dir),
            ("state", config.state_dir),
            ("logs", config.log_dir),
        ):
            if not path.exists():
                missing_paths.append(label)
            elif not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                inaccessible.append(label)
        add(
            "filesystem",
            "error" if inaccessible else "warning" if missing_paths else "ok",
            "Local directory accessibility",
            "Permissions inspected without creating probe files.",
            "Check directory permissions or complete Setup." if inaccessible or missing_paths else "",
            missing=missing_paths,
            inaccessible=inaccessible,
        )
    if upgrade:
        status = doctor_exit_code(checks)
        add(
            "upgrade",
            "error" if status == 2 else "warning" if status == 1 else "ok",
            "Upgrade blocked by errors"
            if status == 2
            else "Review warnings before upgrading"
            if status == 1
            else "Local upgrade checks passed",
            "This preflight did not modify local state or test network connectivity.",
            "Stop InvestOrch, create and verify an offline backup; follow migration and Skill update guidance, then restart. "
            "doctor and upgrade check are read-only; backup create, migrate portfolio and --update write files.",
        )
    return checks


def _storage_checks(sessions: Path, portfolio: Path, add: Any) -> None:
    for code, path in (("sessions", sessions), ("portfolio_schema", portfolio)):
        if not path.exists():
            add(
                code,
                "warning",
                "Database has not been created",
                remediation="Complete Setup for a new installation; restore missing existing data from backup.",
            )
            continue
        try:
            with path.open("rb") as stream:
                header = stream.read(20)
            if header[18:20] == b"\x02\x02" or Path(str(path) + "-wal").exists():
                add(
                    code,
                    "unknown",
                    "WAL database check skipped",
                    "A read-only SQLite connection can modify WAL sidecars.",
                    "Stop all writers, checkpoint and switch to journal_mode=DELETE with the owning application, then rerun doctor.",
                )
                continue
            if code == "portfolio_schema":
                report = migrate_portfolio(path, dry_run=True)
                add(
                    code,
                    "error" if not report.safe else "ok" if report.source_schema_version == 6 else "warning",
                    "Portfolio migration blocked"
                    if not report.safe
                    else f"Portfolio schema v{report.source_schema_version}",
                    report.reason or report.validation_summary,
                    "Stop legacy live deployments using the old version before migration."
                    if not report.safe
                    else "Back up offline, then run investorch migrate portfolio (writes files)."
                    if report.source_schema_version != 6
                    else "",
                    source_schema_version=report.source_schema_version,
                    target_schema_version=report.target_schema_version,
                    safe=report.safe,
                )
            else:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
                    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise sqlite3.DatabaseError("Integrity check failed")
                    connection.execute("SELECT session_id, created_at, updated_at FROM agent_sessions LIMIT 0")
                    connection.execute("SELECT session_id, message_data FROM agent_messages LIMIT 0")
                    connection.execute("SELECT session_id, title, archived_at FROM extra_session_metadata LIMIT 0")
                    connection.execute("SELECT session_id, branch_from_session_id FROM session_lineage LIMIT 0")
                add(code, "ok", "Session schema and basic integrity passed")
        except (OSError, sqlite3.Error, PortfolioStorageError):
            add(
                code,
                "error",
                "Database cannot be read or its schema is invalid",
                remediation="Keep the original database; restore a verified backup or inspect it offline with its original application version.",
            )


def _skill_checks(registry: Path, root: Path, add: Any) -> None:
    if not registry.exists():
        add(
            "skills",
            "warning",
            "Skill registry is missing",
            remediation="Complete Setup or restore the registry from backup.",
        )
        return
    try:
        records = read_registry(registry)
        missing, invalid, drift = [], [], []
        for name, record in records.items():
            installed = root / name
            if not installed.exists():
                missing.append(name)
                continue
            try:
                skill = validate_skill(installed, source_type=record.source.type)
                if record.source.type == "builtin":
                    bundled = BUNDLED_ROOT / name
                    expected = validate_skill(bundled, source_type="builtin")
                    if skill.files != expected.files or any(
                        (installed / file).read_bytes() != (bundled / file).read_bytes() for file in expected.files
                    ):
                        drift.append(name)
            except (ValueError, OSError):
                invalid.append(name)
        for bundled in BUNDLED_ROOT.iterdir():
            if bundled.is_dir() and bundled.name not in records:
                missing.append(bundled.name)
        add(
            "skills",
            "warning" if missing or invalid or drift else "ok",
            "Installed Skill inspection",
            "Content comparison includes files, independent of version strings.",
            "Run investorch --update explicitly for managed built-ins; repair custom Skills or restore missing files."
            if missing or invalid or drift
            else "",
            enabled=sum(record.enabled for record in records.values()),
            missing=missing,
            invalid=invalid,
            builtin_drift=drift,
        )
    except (ValueError, OSError):
        add(
            "skills",
            "error",
            "Skill registry or package resources are invalid",
            remediation="Restore the Skill registry or reinstall the package; do not delete custom Skills.",
        )


def _integration_checks(config: Any, add: Any) -> None:
    try:
        servers = read_mcp_server_configs(config.mcp_config_path)
        enabled = sum(server.get("enabled", True) for server in servers)
        add(
            "mcp",
            "unknown" if enabled else "disabled",
            "MCP configuration valid; connections not tested" if enabled else "No enabled MCP servers",
            enabled=enabled,
            configured=len(servers),
        )
    except (OSError, ValueError, TypeError, NotImplementedError):
        add(
            "mcp",
            "error",
            "MCP configuration is invalid",
            remediation="Repair mcp.toml in Settings Integrations; no server connection was attempted.",
        )
    available = importlib.util.find_spec("rqalpha") is not None
    bundle = config.rqalpha_bundle_dir
    present = bundle.is_dir() and any(bundle.iterdir())
    add(
        "rqalpha",
        "unknown" if available and present else "warning",
        "RQAlpha dependency and bundle present; data not loaded"
        if available and present
        else "RQAlpha dependency or bundle is missing",
        "Bundle existence only; market coverage has not been validated.",
        "Use rqalpha download-bundle after reviewing its download and disk requirements." if not present else "",
        dependency_available=available,
        bundle_present=present,
    )
    if not config["backtest.use_cnequity"]:
        add("cnequity", "disabled", "CNEquity is disabled")
    else:
        available = importlib.util.find_spec("cnequity") is not None
        present = config.cnequity_config_path.is_file()
        add(
            "cnequity",
            "unknown" if available and present else "warning",
            "CNEquity configured; connectivity not tested"
            if available and present
            else "CNEquity dependency or configuration is missing",
            dependency_available=available,
            config_present=present,
        )
