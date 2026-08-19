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
        The latest reported run status, QMT-owned durable background jobs, and bounded stdout/stderr output when job_id is provided. A running job may still have a previous latest-run report; use the job receipt and exit code for that process.
    """
    config = context.context.config
    return await asyncio.to_thread(status, config, config["execution.default_timeout_seconds"], job_id)


@tool(needs_approval=True)
async def data_run(context: RunContextWrapper[AgentContext], operation: DataOperation, trade_date: str | None = None, run_id: str | None = None, full_history: bool = False) -> dict[str, Any]:
    """
    Start a curated-data lifecycle operation in a recoverable background process.

    Operations are initialize, refresh_core, resume, and verify. refresh_core is the foundational/core curated refresh, not a full daily refresh, backfill, live fetch, or arbitrary group operation. Under the strict schema, include every field: pass trade_date=null unless using initialize or refresh_core, run_id=null unless using resume, and full_history=false unless requesting full initialization history. This operation always requires user approval.

    Args:
        operation: One of initialize, refresh_core, resume, or verify.
        trade_date: Nullable YYYY-MM-DD target for initialize or refresh_core; pass null otherwise because the strict schema requires the field.
        run_id: Nullable opaque subsystem run ID required by resume; pass null otherwise because the strict schema requires the field.
        full_history: Boolean that defaults to false; pass false unless initialize should request the full history, because the strict schema requires the field.

    Returns:
        The accepted background job ID, PID, operation, running status, and QMT-owned stdout/stderr log paths. It is a receipt for the background process, not a completed data result; poll data_status with the returned job ID.
    """
    config = context.context.config
    return await asyncio.to_thread(start_operation, config, operation, trade_date, run_id, full_history)
