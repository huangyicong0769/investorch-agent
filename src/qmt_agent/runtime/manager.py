from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agents import SQLiteSession

from qmt_agent.agents import AgentLoop, AgentRunResult, ApprovalOutcome, CompactionResult
from qmt_agent.context import ExecutionState
from qmt_agent.output import OutputEvent

from .models import (
    ActiveRun,
    ApprovalRequest,
    RunOptions,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
    SessionBusyError,
)

logger = logging.getLogger(__name__)

RuntimeOutputHandler = Callable[[RuntimeOutput], Awaitable[None]]
RuntimeApprovalHandler = Callable[[ApprovalRequest], Awaitable[ApprovalOutcome]]
RecordUserMessage = Callable[[str, str], Awaitable[None]]
RuntimeStateHandler = Callable[[RuntimeSessionSnapshot], None]
RunEndedHandler = Callable[[RuntimeRunEnded], Awaitable[None]]


class AgentRuntime:
    """Owns active top-level Agent tasks and their ephemeral session handles."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        execution: ExecutionState,
        sessions_db: Path,
        output_handler: RuntimeOutputHandler,
        approval_handler: RuntimeApprovalHandler,
        record_user_message: RecordUserMessage,
        state_handler: RuntimeStateHandler | None = None,
        run_ended_handler: RunEndedHandler | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self._execution = execution
        self._sessions_db = sessions_db
        self._output_handler = output_handler
        self._approval_handler = approval_handler
        self._record_user_message = record_user_message
        self._state_handler = state_handler
        self._run_ended_handler = run_ended_handler
        self._active_by_session: dict[str, ActiveRun] = {}
        self._active_by_run: dict[str, ActiveRun] = {}
        self._maintenance_sessions: set[str] = set()
        self._closed = False

    def start_run(
        self,
        session_id: str,
        user_input: str,
        options: RunOptions,
    ) -> ActiveRun:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        if session_id in self._active_by_session or session_id in self._maintenance_sessions:
            raise SessionBusyError(f"Session {session_id} already has an active operation")

        run_id = uuid.uuid4().hex
        task = asyncio.create_task(
            self._execute_run(run_id, session_id, user_input, options),
            name=f"agent-run-{run_id}",
        )
        active_run = ActiveRun(
            run_id=run_id,
            session_id=session_id,
            user_input=user_input,
            options=options,
            started_at=datetime.now(timezone.utc),
            task=task,
        )
        self._active_by_session[session_id] = active_run
        self._active_by_run[run_id] = active_run
        self._notify_state(session_id)
        logger.info("Started Agent run session=%s run=%s", session_id, run_id)
        return active_run

    def get_active_run(self, session_id: str) -> ActiveRun | None:
        return self._active_by_session.get(session_id)

    def is_session_active(self, session_id: str) -> bool:
        return session_id in self._active_by_session

    def list_active_runs(self) -> list[ActiveRun]:
        return list(self._active_by_session.values())

    def has_active_runs(self) -> bool:
        return bool(self._active_by_session)

    def session_snapshot(self, session_id: str) -> RuntimeSessionSnapshot:
        active_run = self._active_by_session.get(session_id)
        return RuntimeSessionSnapshot(
            session_id=session_id,
            run_id=active_run.run_id if active_run is not None else None,
            run_started_at=active_run.started_at if active_run is not None else None,
            run_phase=active_run.phase if active_run is not None else None,
            active_follow_up_behavior=(
                active_run.options.follow_up_behavior if active_run is not None else None
            ),
            queued_count=0,
            queue_paused=False,
            pending_steer_count=0,
            todos=tuple(dict(todo) for todo in active_run.todos) if active_run is not None else (),
        )

    @property
    def agent_name(self) -> str:
        return self._agent_loop.agent_name

    @asynccontextmanager
    async def reserve_session(self, session_id: str) -> AsyncIterator[None]:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        if session_id in self._active_by_session or session_id in self._maintenance_sessions:
            raise SessionBusyError(f"Session {session_id} already has an active operation")

        self._maintenance_sessions.add(session_id)
        try:
            yield
        finally:
            self._maintenance_sessions.discard(session_id)

    async def compact_session(self, session_id: str) -> CompactionResult:
        async with self.reserve_session(session_id):
            session: SQLiteSession | None = None
            try:
                session = SQLiteSession(session_id, self._sessions_db)
                return await self._agent_loop.compact(session)
            finally:
                if session is not None:
                    session.close()

    async def aclose(self) -> None:
        self._closed = True
        tasks = [run.task for run in self._active_by_session.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active_by_session.clear()
        self._active_by_run.clear()

    async def _execute_run(
        self,
        run_id: str,
        session_id: str,
        user_input: str,
        options: RunOptions,
    ) -> AgentRunResult:
        session: SQLiteSession | None = None
        result: AgentRunResult | None = None
        status: Literal["completed", "cancelled", "failed"] = "failed"

        async def handle_output(event: OutputEvent) -> None:
            await self._output_handler(
                RuntimeOutput(
                    run_id=run_id,
                    session_id=session_id,
                    event=event,
                )
            )

        async def handle_approval(
            approval_user_input: str,
            tool_name: str,
            arguments: str | None,
        ) -> ApprovalOutcome:
            return await self._approval_handler(
                ApprovalRequest(
                    run_id=run_id,
                    session_id=session_id,
                    user_input=approval_user_input,
                    permission_mode=options.permission_mode,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )

        try:
            session = SQLiteSession(session_id, self._sessions_db)
            await self._record_user_message(session_id, user_input)
            result = await self._agent_loop.run(
                user_input,
                session,
                self._execution,
                run_id=run_id,
                session_id=session_id,
                reasoning_effort=options.reasoning_effort,
                approval_handler=handle_approval,
                output_handler=handle_output,
            )
            status = "completed"
            logger.info("Completed Agent run session=%s run=%s", session_id, run_id)
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            logger.info("Cancelled Agent run session=%s run=%s", session_id, run_id)
            raise
        except Exception:
            logger.exception("Failed Agent run session=%s run=%s", session_id, run_id)
            raise
        finally:
            try:
                if session is not None:
                    session.close()
            finally:
                active_run = self._active_by_run.pop(run_id, None)
                if active_run is not None and self._active_by_session.get(session_id) is active_run:
                    del self._active_by_session[session_id]
                self._notify_state(session_id)
                await self._notify_run_ended(
                    RuntimeRunEnded(
                        session_id=session_id,
                        run_id=run_id,
                        status=status,
                        result=result,
                    )
                )

    def _notify_state(self, session_id: str) -> None:
        if self._state_handler is None:
            return
        try:
            self._state_handler(self.session_snapshot(session_id))
        except Exception:
            logger.exception("Runtime state handler failed for session=%s", session_id)

    async def _notify_run_ended(self, event: RuntimeRunEnded) -> None:
        if self._run_ended_handler is None:
            return
        try:
            await self._run_ended_handler(event)
        except Exception:
            logger.exception(
                "Runtime run-ended handler failed for session=%s run=%s",
                event.session_id,
                event.run_id,
            )
