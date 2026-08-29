from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agents import SQLiteSession

from qmt_agent.agents import AgentLoop, AgentRunResult, ApprovalOutcome, CompactionResult
from qmt_agent.context import ExecutionState
from qmt_agent.output import OutputEvent

from .control import RunControl
from .models import (
    ActiveRun,
    ApprovalRequest,
    FollowUpSubmission,
    PendingSteer,
    QueuedInput,
    RunOptions,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
    SessionBusyError,
)

logger = logging.getLogger(__name__)

RuntimeOutputHandler = Callable[[RuntimeOutput], Awaitable[None]]
RuntimeApprovalHandler = Callable[[ApprovalRequest], Awaitable[ApprovalOutcome]]
RecordUserMessage = Callable[[str, str], Awaitable[int | None]]
RecordUserSteer = Callable[[str, str, str], Awaitable[int | None]]
RuntimeStateHandler = Callable[[RuntimeSessionSnapshot], None]
RunEndedHandler = Callable[[RuntimeRunEnded], Awaitable[None]]
RuntimeFollowUpHandler = Callable[[RuntimeFollowUpEvent], Awaitable[None]]


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
        record_user_steer: RecordUserSteer | None = None,
        follow_up_handler: RuntimeFollowUpHandler | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self._execution = execution
        self._sessions_db = sessions_db
        self._output_handler = output_handler
        self._approval_handler = approval_handler
        self._record_user_message = record_user_message
        self._state_handler = state_handler
        self._run_ended_handler = run_ended_handler
        self._record_user_steer = record_user_steer
        self._follow_up_handler = follow_up_handler
        self._active_by_session: dict[str, ActiveRun] = {}
        self._active_by_run: dict[str, ActiveRun] = {}
        self._controls_by_run: dict[str, RunControl] = {}
        self._steer_fallback_by_session: dict[str, deque[PendingSteer]] = {}
        self._queued_by_session: dict[str, deque[QueuedInput]] = {}
        self._queue_paused_sessions: set[str] = set()
        self._maintenance_sessions: set[str] = set()
        self._closed = False

    def start_run(
        self,
        session_id: str,
        user_input: str,
        options: RunOptions,
    ) -> ActiveRun:
        return self._start_run(session_id, user_input, options)

    def _start_run(
        self,
        session_id: str,
        user_input: str,
        options: RunOptions,
        *,
        record_user_message: bool = True,
        start_gate: asyncio.Event | None = None,
        allow_steer_fallback: bool = False,
        allow_queue_promotion: bool = False,
    ) -> ActiveRun:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        if (
            session_id in self._active_by_session
            or session_id in self._maintenance_sessions
            or (
                not allow_steer_fallback
                and bool(self._steer_fallback_by_session.get(session_id))
            )
            or (
                not allow_queue_promotion
                and bool(self._queued_by_session.get(session_id))
            )
        ):
            raise SessionBusyError(f"Session {session_id} already has an active operation")

        run_id = uuid.uuid4().hex
        run_control = RunControl(
            session_id,
            run_id,
            lambda: self._notify_state(session_id),
        )
        task = asyncio.create_task(
            self._execute_run(
                run_id,
                session_id,
                user_input,
                options,
                run_control,
                record_user_message=record_user_message,
                start_gate=start_gate,
            ),
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
        self._controls_by_run[run_id] = run_control
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
        return bool(self._active_by_session) or any(self._steer_fallback_by_session.values())

    def has_queued_inputs(self, session_id: str | None = None) -> bool:
        if session_id is not None:
            return bool(self._queued_by_session.get(session_id))
        return any(self._queued_by_session.values())

    async def submit_follow_up(
        self,
        session_id: str,
        text: str,
        next_run_options: RunOptions,
    ) -> FollowUpSubmission:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        active_run = self._active_by_session.get(session_id)
        if active_run is None:
            raise SessionBusyError(f"Session {session_id} does not have an active Agent run")
        if not text.strip():
            raise ValueError("Follow-up text must not be empty")

        if active_run.options.follow_up_behavior == "queue":
            queued_input = QueuedInput(
                queue_id=uuid.uuid4().hex,
                session_id=session_id,
                text=text,
                options=next_run_options,
                created_at=datetime.now(timezone.utc),
            )
            self._queued_by_session.setdefault(session_id, deque()).append(queued_input)
            self._notify_state(session_id)
            await self._notify_follow_up(
                RuntimeFollowUpEvent(
                    kind="queue_submitted",
                    session_id=session_id,
                    run_id=active_run.run_id,
                    source_run_id=active_run.run_id,
                    follow_up_id=queued_input.queue_id,
                    text=text,
                    journal_seq=None,
                )
            )
            logger.info(
                "Follow-up submitted behavior=queue session=%s run=%s queue=%s",
                session_id,
                active_run.run_id,
                queued_input.queue_id,
            )
            return FollowUpSubmission(
                session_id=session_id,
                active_run_id=active_run.run_id,
                behavior="queue",
                follow_up_id=queued_input.queue_id,
            )

        control = self._controls_by_run[active_run.run_id]
        steer = control.reserve_steer(text, next_run_options)
        journal_seq = None
        try:
            try:
                if self._record_user_steer is not None:
                    journal_seq = await self._record_user_steer(
                        session_id,
                        active_run.run_id,
                        text,
                    )
            except Exception:
                logger.exception(
                    "Failed to append Steer input to journal session=%s run=%s steer=%s",
                    session_id,
                    active_run.run_id,
                    steer.steer_id,
                )
            await self._notify_follow_up(
                RuntimeFollowUpEvent(
                    kind="steer_submitted",
                    session_id=session_id,
                    run_id=active_run.run_id,
                    source_run_id=active_run.run_id,
                    follow_up_id=steer.steer_id,
                    text=text,
                    journal_seq=journal_seq,
                )
            )
        finally:
            control.mark_ready(steer.steer_id, journal_seq)

        logger.info(
            "Follow-up submitted behavior=steer session=%s run=%s steer=%s",
            session_id,
            active_run.run_id,
            steer.steer_id,
        )
        return FollowUpSubmission(
            session_id=session_id,
            active_run_id=active_run.run_id,
            behavior="steer",
            follow_up_id=steer.steer_id,
        )

    def session_snapshot(self, session_id: str) -> RuntimeSessionSnapshot:
        active_run = self._active_by_session.get(session_id)
        control = self._controls_by_run.get(active_run.run_id) if active_run is not None else None
        return RuntimeSessionSnapshot(
            session_id=session_id,
            run_id=active_run.run_id if active_run is not None else None,
            run_started_at=active_run.started_at if active_run is not None else None,
            run_phase=active_run.phase if active_run is not None else None,
            active_follow_up_behavior=(
                active_run.options.follow_up_behavior if active_run is not None else None
            ),
            queued_count=len(self._queued_by_session.get(session_id, ())),
            queue_paused=session_id in self._queue_paused_sessions,
            pending_steer_count=(
                (control.pending_count() if control is not None else 0)
                + len(self._steer_fallback_by_session.get(session_id, ()))
            ),
            todos=tuple(dict(todo) for todo in active_run.todos) if active_run is not None else (),
        )

    def list_queued_inputs(self, session_id: str) -> list[QueuedInput]:
        return list(self._queued_by_session.get(session_id, ()))

    def remove_queued_input(self, session_id: str, queue_id: str) -> QueuedInput:
        queue = self._queued_by_session.get(session_id)
        if not queue:
            raise KeyError(f"Unknown queued input: {queue_id}")
        for queued_input in queue:
            if queued_input.queue_id == queue_id:
                queue.remove(queued_input)
                if not queue:
                    self._queued_by_session.pop(session_id, None)
                    self._queue_paused_sessions.discard(session_id)
                self._notify_state(session_id)
                return queued_input
        raise KeyError(f"Unknown queued input: {queue_id}")

    def clear_queue(self, session_id: str) -> int:
        queue = self._queued_by_session.pop(session_id, None)
        count = len(queue) if queue is not None else 0
        self._queue_paused_sessions.discard(session_id)
        if count:
            self._notify_state(session_id)
            logger.info("Queue cleared session=%s count=%d", session_id, count)
        return count

    def pause_queue(self, session_id: str) -> None:
        if not self._queued_by_session.get(session_id):
            return
        if session_id not in self._queue_paused_sessions:
            self._queue_paused_sessions.add(session_id)
            self._notify_state(session_id)
            logger.info(
                "Queue paused session=%s count=%d",
                session_id,
                len(self._queued_by_session[session_id]),
            )

    async def resume_queue(self, session_id: str) -> None:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        if session_id in self._active_by_session or session_id in self._maintenance_sessions:
            raise SessionBusyError(f"Session {session_id} already has an active operation")
        if not self._queued_by_session.get(session_id):
            raise ValueError(f"Session {session_id} does not have queued inputs")
        if session_id not in self._queue_paused_sessions:
            raise ValueError(f"Session {session_id} queue is not paused")
        self._queue_paused_sessions.discard(session_id)
        self._notify_state(session_id)
        logger.info(
            "Queue resumed session=%s count=%d",
            session_id,
            len(self._queued_by_session[session_id]),
        )
        await self._try_promote_queue(session_id)

    @property
    def agent_name(self) -> str:
        return self._agent_loop.agent_name

    @asynccontextmanager
    async def reserve_session(
        self,
        session_id: str,
        *,
        allow_pending_work: bool = False,
    ) -> AsyncIterator[None]:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        if (
            session_id in self._active_by_session
            or session_id in self._maintenance_sessions
            or (
                not allow_pending_work
                and (
                    bool(self._steer_fallback_by_session.get(session_id))
                    or bool(self._queued_by_session.get(session_id))
                )
            )
        ):
            raise SessionBusyError(f"Session {session_id} already has an active operation")

        self._maintenance_sessions.add(session_id)
        try:
            yield
        finally:
            self._maintenance_sessions.discard(session_id)
            await self._try_promote_steer_fallback(session_id)
            await self._try_promote_queue(session_id)

    async def compact_session(self, session_id: str) -> CompactionResult:
        async with self.reserve_session(session_id, allow_pending_work=True):
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
        self._controls_by_run.clear()
        self._steer_fallback_by_session.clear()
        self._queued_by_session.clear()
        self._queue_paused_sessions.clear()

    async def _execute_run(
        self,
        run_id: str,
        session_id: str,
        user_input: str,
        options: RunOptions,
        run_control: RunControl,
        *,
        record_user_message: bool,
        start_gate: asyncio.Event | None,
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
            if start_gate is not None:
                await start_gate.wait()
            session = SQLiteSession(session_id, self._sessions_db)
            if record_user_message:
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
                run_control=run_control,
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
                run_control.close_submissions()
                active_run = self._active_by_run.pop(run_id, None)
                if active_run is not None and self._active_by_session.get(session_id) is active_run:
                    del self._active_by_session[session_id]
                self._controls_by_run.pop(run_id, None)
                await run_control.wait_until_ready()
                if status == "completed":
                    fallbacks = run_control.take_fallbacks()
                    if fallbacks:
                        self._steer_fallback_by_session.setdefault(session_id, deque()).extend(fallbacks)
                        logger.info(
                            "Steer terminal fallback session=%s run=%s count=%d",
                            session_id,
                            run_id,
                            len(fallbacks),
                        )
                else:
                    discarded = run_control.discard()
                    if discarded:
                        logger.info(
                            "Discarded unconsumed Steer input session=%s run=%s status=%s count=%d",
                            session_id,
                            run_id,
                            status,
                            discarded,
                        )
                    self.pause_queue(session_id)
                if not self._steer_fallback_by_session.get(session_id):
                    self._notify_state(session_id)
                await self._notify_run_ended(
                    RuntimeRunEnded(
                        session_id=session_id,
                        run_id=run_id,
                        status=status,
                        result=result,
                    )
                )
                await self._try_promote_steer_fallback(session_id)
                await self._try_promote_queue(session_id)

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

    async def _notify_follow_up(self, event: RuntimeFollowUpEvent) -> None:
        if self._follow_up_handler is None:
            return
        try:
            await self._follow_up_handler(event)
        except Exception:
            logger.exception(
                "Runtime follow-up handler failed for session=%s run=%s follow_up=%s kind=%s",
                event.session_id,
                event.run_id,
                event.follow_up_id,
                event.kind,
            )

    async def _try_promote_steer_fallback(self, session_id: str) -> None:
        queue = self._steer_fallback_by_session.get(session_id)
        if (
            self._closed
            or not queue
            or session_id in self._active_by_session
            or session_id in self._maintenance_sessions
        ):
            return

        steer = queue.popleft()
        if not queue:
            self._steer_fallback_by_session.pop(session_id, None)
        start_gate = asyncio.Event()
        try:
            active_run = self._start_run(
                session_id,
                steer.text,
                steer.options,
                record_user_message=False,
                start_gate=start_gate,
                allow_steer_fallback=True,
            )
        except Exception:
            self._steer_fallback_by_session.setdefault(session_id, deque()).appendleft(steer)
            raise

        try:
            await self._notify_follow_up(
                RuntimeFollowUpEvent(
                    kind="steer_fallback_promoted",
                    session_id=session_id,
                    run_id=active_run.run_id,
                    source_run_id=steer.source_run_id,
                    follow_up_id=steer.steer_id,
                    text=steer.text,
                    journal_seq=steer.journal_seq,
                )
            )
        finally:
            start_gate.set()
        logger.info(
            "Promoted Steer fallback session=%s source_run=%s run=%s steer=%s",
            session_id,
            steer.source_run_id,
            active_run.run_id,
            steer.steer_id,
        )

    async def _try_promote_queue(self, session_id: str) -> None:
        queue = self._queued_by_session.get(session_id)
        if (
            self._closed
            or not queue
            or session_id in self._queue_paused_sessions
            or session_id in self._active_by_session
            or session_id in self._maintenance_sessions
        ):
            return

        queued_input = queue.popleft()
        if not queue:
            self._queued_by_session.pop(session_id, None)
        start_gate = asyncio.Event()
        try:
            active_run = self._start_run(
                session_id,
                queued_input.text,
                queued_input.options,
                record_user_message=False,
                start_gate=start_gate,
                allow_queue_promotion=True,
            )
        except Exception:
            self._queued_by_session.setdefault(session_id, deque()).appendleft(queued_input)
            raise

        try:
            journal_seq = await self._record_user_message(session_id, queued_input.text)
            await self._notify_follow_up(
                RuntimeFollowUpEvent(
                    kind="queue_promoted",
                    session_id=session_id,
                    run_id=active_run.run_id,
                    source_run_id=active_run.run_id,
                    follow_up_id=queued_input.queue_id,
                    text=queued_input.text,
                    journal_seq=journal_seq,
                )
            )
        finally:
            start_gate.set()
        logger.info(
            "Queued input promoted session=%s run=%s queue=%s",
            session_id,
            active_run.run_id,
            queued_input.queue_id,
        )
