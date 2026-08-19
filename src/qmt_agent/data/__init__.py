"""Generic facade for the managed market-data subsystem."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from agents.mcp import MCPServer

from qmt_agent.background import list_jobs, read_job_output
from qmt_agent.config import AppConfig

from .cnequity import initialize as initialize_backend
from .cnequity import load_query_servers as load_backend_query_servers
from .cnequity import start_operation as start_backend_operation
from .cnequity import status as backend_status

DataOperation = Literal["initialize", "refresh_core", "resume", "verify"]


def initialize(managed_data_dir: Path, config_root: Path, timeout_seconds: int | float) -> None:
    """Create or validate the managed data configuration and local layout."""
    initialize_backend(managed_data_dir, config_root, timeout_seconds)


def load_query_servers(config_root: Path, timeout_seconds: int | float) -> list[MCPServer]:
    """Return the built-in read-only query MCP servers."""
    return load_backend_query_servers(config_root, timeout_seconds)


def status(config: AppConfig, timeout_seconds: int | float, job_id: str | None = None) -> dict[str, Any]:
    """Return backend-reported status plus QMT-owned operation tracking."""
    try:
        result = backend_status(config.root, timeout_seconds)
    except RuntimeError as exc:
        result = {"state": "unavailable", "error": str(exc)}
    result["jobs"] = list_jobs(config)
    if job_id is not None:
        result["job_output"] = {"job_id": job_id, **read_job_output(config, job_id)}
    return result


def start_operation(config: AppConfig, operation: DataOperation, trade_date: str | None = None, run_id: str | None = None, full_history: bool = False) -> dict[str, Any]:
    """Start one approved, implementation-neutral lifecycle operation."""
    return start_backend_operation(config, operation, trade_date, run_id, full_history)


__all__ = ["DataOperation", "initialize", "load_query_servers", "start_operation", "status"]
