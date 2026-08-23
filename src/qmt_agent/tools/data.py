from __future__ import annotations

import asyncio
from typing import Any

from agents import RunContextWrapper
from agents.decorators import tool

from qmt_agent.context import AgentContext
from qmt_agent.data import DataOperation, start_operation, status


@tool
async def data_status(context: RunContextWrapper[AgentContext], job_id: str | None = None) -> dict[str, Any]:
    """
    Return the latest curated-data run report and persistent lifecycle-job status, optionally with bounded output for one job.

    Args:
        job_id: Optional job ID returned by data_run. The strict schema still requires this nullable field; pass null to list jobs without their log output.

    Returns:
        An object with state no_runs, has_runs, or unavailable, the latest official run report and batch counts when available, QMT-owned durable jobs with progress logs and exit codes, and bounded stdout/stderr when job_id is provided. A running job may still have a previous latest-run report; for verify, read the bounded output with its exit code and do not auto-repair.
    """
    config = context.context.config
    return await asyncio.to_thread(status, config, config["execution.default_timeout_seconds"], job_id)


@tool(needs_approval=True)
async def data_run(context: RunContextWrapper[AgentContext], operation: DataOperation, trade_date: str | None = None, run_id: str | None = None, full_history: bool = False) -> dict[str, Any]:
    """
    Start a curated-data lifecycle operation in a recoverable background process.

    Operations are initialize, refresh_core, resume, and verify. initialize runs only the managed backend's configured foundational initialization phases; it does not imply that every optional or scheduled dataset is populated. refresh_core is the foundational/core curated refresh, not a full daily refresh, backfill, live fetch, or arbitrary group operation. Under the strict schema, include every field: pass trade_date=null unless using initialize or refresh_core, run_id=null unless using resume, and full_history=false unless extending the history of the configured initialization phases. This operation always requires user approval.

    Args:
        operation: One of initialize, refresh_core, resume, or verify.
        trade_date: Nullable YYYY-MM-DD target for initialize or refresh_core; pass null otherwise because the strict schema requires the field.
        run_id: Nullable opaque subsystem run ID required by resume; pass null otherwise because the strict schema requires the field.
        full_history: Boolean that defaults to false; true extends history only for the managed backend's configured initialization phases, not the full data lake, daily refresh, or backfill. Pass false for every other operation because the strict schema requires the field.

    Returns:
        The accepted background job ID, display-only PID, operation, running status, and QMT-owned stdout/stderr log paths. Stop the process only with stop_background_job using the returned job_id; never signal the displayed PID. It is a receipt for the background process, not a completed data result; poll data_status with the returned job ID to inspect progress and exit_code. A non-zero verify exit may report diagnostics and must not trigger automatic repair.
    """
    config = context.context.config
    return await asyncio.to_thread(start_operation, config, operation, trade_date, run_id, full_history)
