"""Transitional CNEquity wrapper for the curated-data subsystem."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from agents.mcp import MCPServer, MCPServerStdio
from cnequity.config import write_user_config

from qmt_agent.background import start_job
from qmt_agent.config import AppConfig, ConfigError

CONFIG_FILENAME = "cnequity.toml"
DATA_SUBDIRECTORY = "cnequity"
DataOperation = Literal["initialize", "refresh", "resume", "verify"]


def initialize(managed_data_dir: Path, config_root: Path, timeout_seconds: int | float) -> None:
    """Bootstrap the backend config and local layout without ingestion."""
    managed_data_dir = managed_data_dir.expanduser().resolve()
    config_root = config_root.expanduser().resolve()
    if managed_data_dir.exists() and not managed_data_dir.is_dir():
        raise ConfigError(f"data is not a directory: {managed_data_dir}")

    config_path = config_root / CONFIG_FILENAME

    if not config_path.exists():
        try:
            write_user_config(config_path, data_root=str(managed_data_dir / DATA_SUBDIRECTORY), force=False)
        except FileExistsError:
            pass
        except Exception as exc:
            raise ConfigError(f"Unable to create managed data configuration: {config_path}: {exc}") from exc

    commands = (
        ["config", "validate", "--config", str(config_path)],
        ["doctor", "--json", "--config", str(config_path)],
        ["init", "--layout-only", "--config", str(config_path)],
    )
    for command in commands:
        _run_bootstrap_command(config_root, command, timeout_seconds)


def load_query_servers(config_root: Path, timeout_seconds: int | float) -> list[MCPServer]:
    """Build the read-only stdio query server without probing lake readiness."""
    config_root = config_root.expanduser().resolve()
    config_path = (config_root / CONFIG_FILENAME).resolve()
    params = {
        "command": sys.executable,
        "args": ["-m", "cnequity", "mcp", "--config", str(config_path)],
        "cwd": str(config_root),
    }
    return [
        MCPServerStdio(
            params=params,
            name="Managed market-data query",
            cache_tools_list=True,
            client_session_timeout_seconds=timeout_seconds,
        )
    ]


def status(config_root: Path, timeout_seconds: int | float) -> dict[str, Any]:
    """Read the latest backend-owned run summary through the official CLI."""
    config_root = config_root.expanduser().resolve()
    config_path = (config_root / CONFIG_FILENAME).resolve()
    command = [sys.executable, "-m", "cnequity", "status", "--config", str(config_path)]
    try:
        result = subprocess.run(command, cwd=config_root, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Unable to read curated-data status: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"Unable to read curated-data status: {detail}")
    output = result.stdout.strip()
    if output == "No runs yet.":
        return {"state": "not_initialized", "latest_run": None}
    try:
        summary = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Curated-data status returned an invalid response") from exc
    if not isinstance(summary, dict) or not isinstance(summary.get("run"), dict):
        raise RuntimeError("Curated-data status returned an invalid response")
    run = summary["run"]
    latest_run = {"run_id": run.get("run_id"), "status": run.get("status"), "started_at": run.get("started_at"), "finished_at": run.get("finished_at"), "rows_read": run.get("rows_read"), "rows_written": run.get("rows_written"), "error": run.get("error_message")}
    return {"state": "initialized", "latest_run": latest_run}


def start_operation(config: AppConfig, operation: DataOperation, trade_date: str | None = None, run_id: str | None = None, full_history: bool = False) -> dict[str, Any]:
    """Map a generic lifecycle operation to the official backend CLI."""
    config_root = config.root
    config_path = (config_root / CONFIG_FILENAME).resolve()
    command = [sys.executable, "-m", "cnequity"]
    if operation == "initialize":
        command.extend(["init", "--config", str(config_path), "--profile", "full" if full_history else "quick", "--quiet"])
        if trade_date:
            command.extend(["--trade-date", trade_date])
    elif operation == "refresh":
        command.extend(["run", "daily", "--config", str(config_path), "--quiet"])
        if trade_date:
            command.extend(["--trade-date", trade_date])
    elif operation == "resume":
        if not run_id:
            raise ValueError("run_id is required for resume")
        command.extend(["retry", "--config", str(config_path), "--run-id", run_id])
    elif operation == "verify":
        command.extend(["verify", "--config", str(config_path)])
    else:
        raise ValueError(f"Unsupported curated-data operation: {operation}")

    if operation != "resume" and run_id is not None:
        raise ValueError("run_id is only valid for resume")
    if operation not in ("initialize", "refresh") and trade_date is not None:
        raise ValueError("trade_date is only valid for initialize or refresh")
    if operation != "initialize" and full_history:
        raise ValueError("full_history is only valid for initialize")
    return start_job(config, operation, command, config_root)


def _run_bootstrap_command(config_root: Path, command: list[str], timeout_seconds: int | float) -> None:
    try:
        result = subprocess.run([sys.executable, "-m", "cnequity", *command], cwd=config_root, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError(f"Unable to run curated-data bootstrap command {' '.join(command)}: {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise ConfigError(f"Curated-data bootstrap command {' '.join(command)} failed: {detail}")
