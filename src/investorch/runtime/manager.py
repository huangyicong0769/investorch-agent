from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents import SQLiteSession

from investorch.agents import (
    AgentLoop,
    AgentRunResult,
    ApprovalOutcome,
    CompactionResult,
)
from investorch.context import ExecutionState, TodoItem
from investorch.output import OutputEvent

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
RecordUserMessage = Callable[[str, str], Awaitable[int]]
RecordUserSteer = Callable[[str, str, str], Awaitable[int]]
RecordUserSteersActivated = Callable[[str, str, tuple[int, ...]], Awaitable[int]]
RecordUserSteersDiscarded = Callable[[str, str, tuple[int, ...]], Awaitable[int]]
RuntimeStateHandler = Callable[[RuntimeSessionSnapshot], None]
RunEndedHandler = Callable[[RuntimeRunEnded], Awaitable[None]]
RuntimeFollowUpHandler = Callable[[RuntimeFollowUpEvent], Awaitable[None]]


async def _await_journal_write(awaitable: Awaitable[int]) -> tuple[int, asyncio.CancelledError | None]:
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
    return task.result(), cancellation


class _InputJournalBarrier:
    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.error: BaseException | None = None
        self.instruction_head_seq: int | None = None

    async def wait(self) -> int | None:
        await self.event.wait()
        if self.error is not None:
            raise SessionBusyError("The Run failed before its initial input was journaled") from self.error
        return self.instruction_head_seq

    def succeed(self, instruction_head_seq: int | None) -> None:
        if not self.event.is_set():
            self.instruction_head_seq = instruction_head_seq
            self.event.set()

    def fail(self, error: BaseException) -> None:
        if not self.event.is_set():
            self.error = error
            self.event.set()


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
        record_user_steer: RecordUserSteer,
        record_user_steers_activated: RecordUserSteersActivated,
        record_user_steers_discarded: RecordUserSteersDiscarded,
        state_handler: RuntimeStateHandler | None = None,
        run_ended_handler: RunEndedHandler | None = None,
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
        self._record_user_steers_activated = record_user_steers_activated
        self._record_user_steers_discarded = record_user_steers_discarded
        self._follow_up_handler = follow_up_handler
        self._active_by_session: dict[str, ActiveRun] = {}
        self._active_by_run: dict[str, ActiveRun] = {}
        self._run_tasks: set[asyncio.Task[AgentRunResult]] = set()
        self._promotion_tasks: set[asyncio.Task[object]] = set()
        self._submission_tasks: set[asyncio.Task[object]] = set()
        self._controls_by_run: dict[str, RunControl] = {}
        self._input_journal_by_run: dict[str, _InputJournalBarrier] = {}
        self._steer_fallback_by_session: dict[str, deque[PendingSteer]] = {}
        self._queued_by_session: dict[str, deque[QueuedInput]] = {}
        self._queue_paused_sessions: set[str] = set()
        self._maintenance_sessions: set[str] = set()
        self._closed = False

    def start_run(self, session_id: str, user_input: str, options: RunOptions) -> ActiveRun:
        return self._start_run(session_id, user_input, options)

    def start_contextual_run(
        self,
        session_id: str,
        user_input: str,
        application_instruction: str,
        options: RunOptions,
    ) -> ActiveRun:
        if not user_input.strip():
            raise ValueError("User input must not be empty")
        if not application_instruction.strip():
            raise ValueError("Application instruction must not be empty")
        return self._start_run(
            session_id,
            user_input,
            options,
            application_instruction=application_instruction,
        )

    def start_application_run(
        self,
        session_id: str,
        application_instruction: str,
        options: RunOptions,
    ) -> ActiveRun:
        if not application_instruction.strip():
            raise ValueError("Application instruction must not be empty")
        start_gate = asyncio.Event()
        active_run = self._start_run(
            session_id,
            "",
            options,
            record_user_message=False,
            start_gate=start_gate,
            application_instruction=application_instruction,
        )
        self._input_journal_by_run[active_run.run_id].succeed(None)
        start_gate.set()
        return active_run

    def _start_run(
        self,
        session_id: str,
        user_input: str,
        options: RunOptions,
        *,
        record_user_message: bool = True,
        start_gate: asyncio.Event | None = None,
        application_instruction: str | None = None,
        allow_steer_fallback: bool = False,
        allow_queue_promotion: bool = False,
    ) -> ActiveRun:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        if (
            session_id in self._active_by_session
            or session_id in self._maintenance_sessions
            or (not allow_steer_fallback and bool(self._steer_fallback_by_session.get(session_id)))
            or (not allow_queue_promotion and bool(self._queued_by_session.get(session_id)))
        ):
            raise SessionBusyError(f"Session {session_id} already has an active operation")

        run_id = uuid.uuid4().hex
        started_at = datetime.now(UTC)
        run_control = RunControl(session_id, run_id, lambda: self._notify_state(session_id))
        input_journal = _InputJournalBarrier()
        self._input_journal_by_run[run_id] = input_journal
        task = asyncio.create_task(
            self._execute_run(
                run_id,
                session_id,
                user_input,
                options,
                run_control,
                input_journal,
                started_at,
                record_user_message=record_user_message,
                start_gate=start_gate,
                application_instruction=application_instruction,
            ),
            name=f"agent-run-{run_id}",
        )
        self._run_tasks.add(task)
        task.add_done_callback(self._run_tasks.discard)
        active_run = ActiveRun(
            run_id=run_id,
            session_id=session_id,
            user_input=user_input,
            options=options,
            started_at=started_at,
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

    def cancel_run(self, session_id: str) -> ActiveRun:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        active_run = self._active_by_session.get(session_id)
        if active_run is None:
            raise SessionBusyError(f"Session {session_id} does not have an active Agent run")

        control = self._controls_by_run[active_run.run_id]
        active_run.phase = "stopping"
        active_run.stopped_pending_steer_count = control.pending_count()
        if self._queued_by_session.get(session_id):
            self.pause_queue(session_id)
        control.close_submissions()
        self._notify_state(session_id)
        asyncio.get_running_loop().call_soon(active_run.task.cancel)
        logger.info(
            "Run cancelled by user session=%s run=%s pending_steer=%d",
            session_id,
            active_run.run_id,
            active_run.stopped_pending_steer_count,
        )
        return active_run

    def has_active_runs(self) -> bool:
        return bool(self._active_by_session) or any(self._steer_fallback_by_session.values())

    def has_queued_inputs(self, session_id: str | None = None) -> bool:
        if session_id is not None:
            return bool(self._queued_by_session.get(session_id))
        return any(self._queued_by_session.values())

    async def submit_follow_up(self, session_id: str, text: str, next_run_options: RunOptions) -> FollowUpSubmission:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Follow-up submission requires an asyncio task")
        self._submission_tasks.add(task)
        try:
            return await self._submit_follow_up(session_id, text, next_run_options)
        finally:
            self._submission_tasks.discard(task)

    async def _submit_follow_up(self, session_id: str, text: str, next_run_options: RunOptions) -> FollowUpSubmission:
        if self._closed:
            raise RuntimeError("Agent runtime is closed")
        active_run = self._active_by_session.get(session_id)
        if active_run is None:
            raise SessionBusyError(f"Session {session_id} does not have an active Agent run")
        if active_run.phase == "stopping":
            raise SessionBusyError(f"Session {session_id} Agent run is stopping and cannot accept follow-up input")
        if not text.strip():
            raise ValueError("Follow-up text must not be empty")
        input_journal = self._input_journal_by_run[active_run.run_id]
        await input_journal.wait()
        if self._closed or self._active_by_session.get(session_id) is not active_run or active_run.phase == "stopping":
            raise SessionBusyError(f"Session {session_id} Agent run no longer accepts follow-up input")

        if active_run.options.follow_up_behavior == "queue":
            queued_input = QueuedInput(
                queue_id=uuid.uuid4().hex,
                session_id=session_id,
                text=text,
                options=next_run_options,
                created_at=datetime.now(UTC),
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
        try:
            journal_seq, cancellation = await _await_journal_write(
                self._record_user_steer(session_id, active_run.run_id, text)
            )
        except BaseException:
            control.discard_submission(steer.steer_id)
            logger.exception(
                "Failed to append Steer input to journal session=%s run=%s steer=%s",
                session_id,
                active_run.run_id,
                steer.steer_id,
            )
            raise
        if cancellation is not None:
            control.mark_ready(steer.steer_id, journal_seq)
            raise cancellation

        try:
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
            session_id=session_id, active_run_id=active_run.run_id, behavior="steer", follow_up_id=steer.steer_id
        )

    def session_snapshot(self, session_id: str) -> RuntimeSessionSnapshot:
        active_run = self._active_by_session.get(session_id)
        control = self._controls_by_run.get(active_run.run_id) if active_run is not None else None
        return RuntimeSessionSnapshot(
            session_id=session_id,
            run_id=active_run.run_id if active_run is not None else None,
            run_started_at=active_run.started_at if active_run is not None else None,
            run_phase=active_run.phase if active_run is not None else None,
            active_follow_up_behavior=(active_run.options.follow_up_behavior if active_run is not None else None),
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
            logger.info("Queue paused session=%s count=%d", session_id, len(self._queued_by_session[session_id]))

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
        logger.info("Queue resumed session=%s count=%d", session_id, len(self._queued_by_session[session_id]))
        await self._try_promote_queue(session_id)

    @property
    def agent_name(self) -> str:
        return self._agent_loop.agent_name

    @asynccontextmanager
    async def reserve_session(self, session_id: str, *, allow_pending_work: bool = False) -> AsyncIterator[None]:
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
        tasks = list(self._run_tasks | self._promotion_tasks | self._submission_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active_by_session.clear()
        self._active_by_run.clear()
        self._controls_by_run.clear()
        self._input_journal_by_run.clear()
        for session_id, pending in self._steer_fallback_by_session.items():
            if pending:
                logger.info("Discarding Steer fallback during shutdown session=%s count=%d", session_id, len(pending))
        for session_id, queued in self._queued_by_session.items():
            if queued:
                logger.info("Discarding queued input during shutdown session=%s count=%d", session_id, len(queued))
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
        input_journal: _InputJournalBarrier,
        started_at: datetime,
        *,
        record_user_message: bool,
        start_gate: asyncio.Event | None,
        application_instruction: str | None,
    ) -> AgentRunResult:
        session: SQLiteSession | None = None
        result: AgentRunResult | None = None
        status: Literal["completed", "cancelled", "failed"] = "failed"
        discarded_steer_count = 0
        discarded_steer_seqs: tuple[int, ...] = ()

        async def handle_output(event: OutputEvent) -> None:
            await self._output_handler(RuntimeOutput(run_id=run_id, session_id=session_id, event=event))

        async def handle_approval(
            instruction_head_seq: int | None,
            tool_name: str,
            arguments: str | None,
        ) -> ApprovalOutcome:
            active_run = self._active_by_run.get(run_id)
            if (
                active_run is not None
                and active_run.session_id == session_id
                and self._active_by_session.get(session_id) is active_run
            ):
                active_run.phase = "waiting_approval"
                self._notify_state(session_id)
            try:
                return await self._approval_handler(
                    ApprovalRequest(
                        approval_id=uuid.uuid4().hex,
                        run_id=run_id,
                        session_id=session_id,
                        user_input=user_input,
                        permission_mode=options.permission_mode,
                        tool_name=tool_name,
                        arguments=arguments,
                        instruction_head_seq=instruction_head_seq,
                    )
                )
            finally:
                if (
                    active_run is not None
                    and self._active_by_run.get(run_id) is active_run
                    and self._active_by_session.get(session_id) is active_run
                    and active_run.phase == "waiting_approval"
                ):
                    active_run.phase = "running"
                    self._notify_state(session_id)

        async def handle_todo_update(todos: list[TodoItem]) -> None:
            active_run = self._active_by_run.get(run_id)
            if active_run is None or active_run.session_id != session_id:
                return
            active_run.todos = [dict(todo) for todo in todos]
            self._notify_state(session_id)
            logger.info("Todo updated session=%s run=%s count=%d", session_id, run_id, len(todos))

        async def handle_steers_activated(
            steer_seqs: tuple[int, ...],
        ) -> tuple[int, asyncio.CancelledError | None]:
            try:
                activation_seq, cancellation = await _await_journal_write(
                    self._record_user_steers_activated(session_id, run_id, steer_seqs)
                )
            except Exception:
                logger.exception(
                    "Failed to record activated Steer inputs session=%s run=%s",
                    session_id,
                    run_id,
                )
                raise
            return activation_seq, cancellation

        try:
            if start_gate is not None:
                await start_gate.wait()
            session = SQLiteSession(session_id, self._sessions_db)
            if record_user_message:
                instruction_head_seq = await self._record_user_message(session_id, user_input)
                input_journal.succeed(instruction_head_seq)
            else:
                instruction_head_seq = await input_journal.wait()
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
                todo_update_handler=handle_todo_update,
                instruction_head_seq=instruction_head_seq,
                steer_activated_handler=handle_steers_activated,
                application_instruction=application_instruction,
            )
            status = "completed"
            logger.info("Completed Agent run session=%s run=%s", session_id, run_id)
            return result
        except asyncio.CancelledError as error:
            input_journal.fail(error)
            status = "cancelled"
            logger.info("Cancelled Agent run session=%s run=%s", session_id, run_id)
            raise
        except Exception as error:
            input_journal.fail(error)
            logger.exception("Failed Agent run session=%s run=%s", session_id, run_id)
            raise
        finally:
            try:
                if session is not None:
                    session.close()
            finally:
                active_run = self._active_by_run.get(run_id)
                if active_run is not None and self._active_by_session.get(session_id) is active_run:
                    active_run.phase = "stopping"
                run_control.close_submissions()
                await run_control.wait_until_ready()
                if status == "completed":
                    fallbacks = run_control.take_fallbacks()
                    if fallbacks:
                        self._steer_fallback_by_session.setdefault(session_id, deque()).extend(fallbacks)
                        logger.info(
                            "Steer terminal fallback session=%s run=%s count=%d", session_id, run_id, len(fallbacks)
                        )
                else:
                    discarded_steers = run_control.discard()
                    discarded_steer_count = len(discarded_steers)
                    if discarded_steer_count:
                        discarded_steer_seqs = tuple(
                            steer.journal_seq for steer in discarded_steers if steer.journal_seq is not None
                        )
                        if len(discarded_steer_seqs) != discarded_steer_count:
                            logger.error(
                                "Cannot record discarded Steer inputs without journal sequences session=%s run=%s",
                                session_id,
                                run_id,
                            )
                        else:
                            try:
                                await _await_journal_write(
                                    self._record_user_steers_discarded(session_id, run_id, discarded_steer_seqs)
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to record discarded Steer inputs session=%s run=%s",
                                    session_id,
                                    run_id,
                                )
                        logger.info(
                            "Discarded unconsumed Steer input session=%s run=%s status=%s count=%d",
                            session_id,
                            run_id,
                            status,
                            discarded_steer_count,
                        )
                    self.pause_queue(session_id)
                self._active_by_run.pop(run_id, None)
                if active_run is not None and self._active_by_session.get(session_id) is active_run:
                    del self._active_by_session[session_id]
                self._controls_by_run.pop(run_id, None)
                self._input_journal_by_run.pop(run_id, None)
                if not self._steer_fallback_by_session.get(session_id):
                    self._notify_state(session_id)
                ended_at = datetime.now(UTC)
                await self._notify_run_ended(
                    RuntimeRunEnded(
                        session_id=session_id,
                        run_id=run_id,
                        status=status,
                        started_at=started_at,
                        ended_at=ended_at,
                        result=result,
                        discarded_steer_count=discarded_steer_count,
                        discarded_steer_seqs=discarded_steer_seqs,
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
            logger.exception("Runtime run-ended handler failed for session=%s run=%s", event.session_id, event.run_id)

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
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Follow-up promotion requires an asyncio task")
        self._promotion_tasks.add(task)
        try:
            await self._promote_steer_fallback(session_id)
        finally:
            self._promotion_tasks.discard(task)

    async def _promote_steer_fallback(self, session_id: str) -> None:
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
        input_journal = self._input_journal_by_run[active_run.run_id]
        cancellation: asyncio.CancelledError | None = None

        try:
            if steer.journal_seq is None:
                raise RuntimeError("Steer fallback does not have a durable journal sequence")
            try:
                activation_seq, cancellation = await _await_journal_write(
                    self._record_user_steers_activated(session_id, active_run.run_id, (steer.journal_seq,))
                )
            except BaseException as error:
                logger.exception(
                    "Failed to record promoted Steer activation session=%s run=%s steer=%s",
                    session_id,
                    active_run.run_id,
                    steer.steer_id,
                )
                input_journal.fail(error)
                disposition_cancellation: asyncio.CancelledError | None = None
                try:
                    _, disposition_cancellation = await _await_journal_write(
                        self._record_user_steers_discarded(
                            session_id,
                            steer.source_run_id,
                            (steer.journal_seq,),
                        )
                    )
                except Exception:
                    logger.exception(
                        "Failed to record abandoned Steer fallback session=%s source_run=%s steer=%s",
                        session_id,
                        steer.source_run_id,
                        steer.steer_id,
                    )
                active_run.task.cancel()
                start_gate.set()
                await asyncio.gather(active_run.task, return_exceptions=True)
                if isinstance(error, asyncio.CancelledError):
                    raise
                if disposition_cancellation is not None:
                    raise disposition_cancellation from error
                return
            input_journal.succeed(activation_seq)
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
        if cancellation is not None:
            raise cancellation
        logger.info(
            "Promoted Steer fallback session=%s source_run=%s run=%s steer=%s",
            session_id,
            steer.source_run_id,
            active_run.run_id,
            steer.steer_id,
        )

    async def _try_promote_queue(self, session_id: str) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Follow-up promotion requires an asyncio task")
        self._promotion_tasks.add(task)
        try:
            await self._promote_queue(session_id)
        finally:
            self._promotion_tasks.discard(task)

    async def _promote_queue(self, session_id: str) -> None:
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
        input_journal = self._input_journal_by_run[active_run.run_id]

        await asyncio.sleep(0)
        try:
            journal_seq, cancellation = await _await_journal_write(
                self._record_user_message(session_id, queued_input.text)
            )
        except BaseException as error:
            input_journal.fail(error)
            self._queued_by_session.setdefault(session_id, deque()).appendleft(queued_input)
            self.pause_queue(session_id)
            active_run.task.cancel()
            start_gate.set()
            await asyncio.gather(active_run.task, return_exceptions=True)
            if isinstance(error, asyncio.CancelledError):
                raise
            logger.exception(
                "Queued input promotion failed before Agent execution session=%s run=%s queue=%s",
                session_id,
                active_run.run_id,
                queued_input.queue_id,
            )
            return

        input_journal.succeed(journal_seq)
        if cancellation is not None:
            start_gate.set()
            logger.info(
                "Queued input journal committed during cancelled promotion session=%s run=%s queue=%s",
                session_id,
                active_run.run_id,
                queued_input.queue_id,
            )
            raise cancellation
        try:
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
            "Queued input promoted session=%s run=%s queue=%s", session_id, active_run.run_id, queued_input.queue_id
        )
