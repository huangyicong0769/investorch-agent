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
    Return the latest curated-data run and persistent lifecycle-job status, optionally with bounded output for one job.

    Args:
        job_id: Optional job ID returned by data_run. Defaults to null, which lists jobs without their log output.

    Returns:
        The normalized latest run status, durable background jobs, and bounded output when job_id is provided.
    """
    config = context.context.config
    return await asyncio.to_thread(status, config, config["execution.default_timeout_seconds"], job_id)


@tool(needs_approval=True)
async def data_run(context: RunContextWrapper[AgentContext], operation: DataOperation, trade_date: str | None = None, run_id: str | None = None, full_history: bool = False) -> dict[str, Any]:
    """
    Start a curated-data lifecycle operation in a recoverable background process.

    Operations are initialize, refresh, resume, and verify. Use trade_date only with initialize or refresh, run_id only with resume, and full_history only with initialize. This operation always requires user approval.

    Args:
        operation: One of initialize, refresh, resume, or verify.
        trade_date: Optional YYYY-MM-DD target for initialize or refresh. Defaults to null.
        run_id: Optional opaque subsystem run ID. It is required by resume. Defaults to null otherwise.
        full_history: Request the subsystem's full initialization history instead of its default initial window. Defaults to false.

    Returns:
        The accepted background job ID, PID, operation, running status, and log paths.
    """
    config = context.context.config
    return await asyncio.to_thread(start_operation, config, operation, trade_date, run_id, full_history)
