from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AsyncExitStack

from agents import SQLiteSession

from investorch.agents import CompactionResult, session_history_restore_failed
from investorch.config import AppConfig
from investorch.journal import SessionJournal
from investorch.runtime import AgentRuntime, SessionBusyError
from investorch.storage import (
    SessionDeletionScopeChangedError,
    SessionExternalForkError,
    SessionRecord,
    add_session_related_portfolio_ids,
    archive_session,
    create_session,
    delete_session_transaction,
    delete_unused_session,
    fork_session,
    get_session,
    get_session_deletion_scope,
    get_session_related_portfolio_ids,
    is_application_workflow_started,
    is_session_archived,
    list_sessions,
    mark_application_workflow_started,
    set_session_title,
    unarchive_session,
)

from .presentation_state import SessionPresentationStore

logger = logging.getLogger(__name__)


async def _await_delete_transaction(
    awaitable: Awaitable[None],
    *,
    on_cancel: Callable[[], None],
) -> tuple[bool, BaseException | None]:
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    worker_error: BaseException | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                on_cancel()
                cancellation = error
        except BaseException as error:
            worker_error = error
            break
    try:
        task.result()
    except BaseException as error:
        return False, cancellation or worker_error or error
    return True, cancellation


def _cancel_delete_transaction(cancel_event: threading.Event, commit_lock: threading.Lock) -> None:
    with commit_lock:
        cancel_event.set()


class SessionArchivedError(RuntimeError):
    pass


class SessionNotFoundError(RuntimeError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session {session_id} does not exist")


class SessionHasQueuedInputsError(RuntimeError):
    pass


class SessionHasChildrenError(RuntimeError):
    pass


class SessionDeletionCleanupError(RuntimeError):
    def __init__(self, session_ids: tuple[str, ...]) -> None:
        self.deleted_session_ids = session_ids
        super().__init__("Session identities were deleted, but history cleanup is incomplete. Check System Status.")


class SessionDeletionUnsafeError(RuntimeError):
    pass


class SessionClearReplacementError(RuntimeError):
    def __init__(self, session_ids: tuple[str, ...]) -> None:
        self.deleted_session_ids = session_ids
        self.replacement_session_id = None
        super().__init__(
            "Sessions were deleted, but creating the new Session failed. Select or create a Session; check System Status."
        )


class SessionAlreadyArchivedError(RuntimeError):
    pass


class SessionCompactionError(RuntimeError):
    def __init__(self, *, consistency_uncertain: bool) -> None:
        self.consistency_uncertain = consistency_uncertain


class SessionOperations:
    def __init__(
        self,
        *,
        config: AppConfig,
        runtime: AgentRuntime,
        journal: SessionJournal,
        presentation_state: SessionPresentationStore,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._journal = journal
        self._presentation_state = presentation_state
        self.lifecycle_lock = asyncio.Lock()

    async def create(self) -> str:
        session_id = uuid.uuid4().hex
        await asyncio.to_thread(create_session, self._config.sessions_db, session_id)
        logger.info("Started session %s", session_id)
        return session_id

    async def create_for_request(self, workflow: str, request_id: str) -> tuple[str, bool]:
        if not workflow.strip() or not request_id.strip():
            raise ValueError("workflow and request_id must not be empty")
        session_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"urn:investorch:session-request:{workflow.strip()}:{request_id.strip()}",
        ).hex
        created = await asyncio.to_thread(create_session, self._config.sessions_db, session_id)
        if created:
            logger.info("Started idempotent session %s for workflow %s", session_id, workflow)
        return session_id, created

    async def has_agent_activity(self, session_id: str) -> bool:
        if await self._journal.session_exists(session_id):
            return True
        session = SQLiteSession(session_id, self._config.sessions_db)
        try:
            return bool(await session.get_items(limit=1))
        finally:
            session.close()

    async def is_application_workflow_started(self, session_id: str) -> bool:
        try:
            return await asyncio.to_thread(
                is_application_workflow_started,
                self._config.sessions_db,
                session_id,
            )
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    async def mark_application_workflow_started(self, session_id: str) -> None:
        try:
            await asyncio.to_thread(
                mark_application_workflow_started,
                self._config.sessions_db,
                session_id,
            )
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    async def get_related_portfolio_ids(self, session_id: str) -> tuple[str, ...]:
        try:
            return await asyncio.to_thread(
                get_session_related_portfolio_ids,
                self._config.sessions_db,
                session_id,
            )
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    async def add_related_portfolio_ids(
        self,
        session_id: str,
        portfolio_ids: Iterable[str],
    ) -> tuple[str, ...]:
        try:
            return await asyncio.to_thread(
                add_session_related_portfolio_ids,
                self._config.sessions_db,
                session_id,
                tuple(portfolio_ids),
            )
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    async def discard_if_unused(self, session_id: str) -> bool:
        if self._runtime.has_queued_inputs(session_id):
            return False
        try:
            async with self.lifecycle_lock, self._runtime.reserve_session(session_id):
                if await self._journal.session_exists(session_id):
                    return False
                deleted = await asyncio.to_thread(delete_unused_session, self._config.sessions_db, session_id)
        except SessionBusyError:
            return False
        except Exception:
            logger.exception("Failed to discard unused session %s", session_id)
            return False
        if deleted:
            self._runtime.session_models.discard(session_id)
            logger.info("Discarded unused session %s", session_id)
        return deleted

    async def archive(self, session_id: str) -> None:
        if self._runtime.has_queued_inputs(session_id):
            raise SessionHasQueuedInputsError
        async with self.lifecycle_lock, self._runtime.reserve_session(session_id):
            if await asyncio.to_thread(is_session_archived, self._config.sessions_db, session_id):
                raise SessionAlreadyArchivedError
            await asyncio.to_thread(archive_session, self._config.sessions_db, session_id)
        logger.info("Session archived session=%s", session_id)

    async def unarchive(self, session_id: str) -> None:
        async with self.lifecycle_lock:
            await asyncio.to_thread(unarchive_session, self._config.sessions_db, session_id)
        logger.info("Session unarchived session=%s", session_id)

    async def fork(self, source_session_id: str) -> str:
        if self._runtime.has_queued_inputs(source_session_id):
            raise SessionHasQueuedInputsError
        target_session_id = uuid.uuid4().hex
        while target_session_id == source_session_id:
            target_session_id = uuid.uuid4().hex
        async with (
            self.lifecycle_lock,
            self._runtime.reserve_session(source_session_id),
            self._runtime.reserve_session(target_session_id),
        ):
            source_draft = self._runtime.session_models.draft(source_session_id)
            await fork_session(
                source_session_id=source_session_id,
                target_session_id=target_session_id,
                sessions_db=self._config.sessions_db,
                journal=self._journal,
            )
            if source_draft is not None:
                self._runtime.session_models.set_draft(target_session_id, source_draft)
        return target_session_id

    async def set_title(self, session_id: str, title: str) -> SessionRecord:
        async with self.lifecycle_lock, self._runtime.reserve_session(session_id):
            session = await asyncio.to_thread(get_session, self._config.sessions_db, session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            if session.archived_at is not None:
                raise SessionArchivedError
            await asyncio.to_thread(set_session_title, self._config.sessions_db, session_id, title)
            updated = await asyncio.to_thread(get_session, self._config.sessions_db, session_id)
            if updated is None:
                raise SessionNotFoundError(session_id)
        logger.info("Updated title for session %s", session_id)
        return updated

    async def clear(self, session_id: str, *, expected_session_ids: tuple[str, ...] | None = None) -> str:
        async with self.lifecycle_lock:
            if await asyncio.to_thread(is_session_archived, self._config.sessions_db, session_id):
                raise SessionArchivedError
            deleted_ids = await self._delete(
                session_id,
                expected_session_ids=(session_id,) if expected_session_ids is None else expected_session_ids,
            )
            creation = asyncio.create_task(self.create())
            cancelled: asyncio.CancelledError | None = None
            while not creation.done():
                try:
                    await asyncio.shield(creation)
                except asyncio.CancelledError as error:
                    cancelled = error
                except Exception:
                    break
            try:
                replacement_session_id = creation.result()
            except (Exception, asyncio.CancelledError) as error:
                logger.exception("Clear deletion committed session_ids=%s but replacement creation failed", deleted_ids)
                raise SessionClearReplacementError(deleted_ids) from error
            logger.info("Clear deletion committed session_ids=%s replacement=%s", deleted_ids, replacement_session_id)
            if cancelled is not None:
                raise cancelled
        return replacement_session_id

    async def deletion_preview(self, session_id: str) -> dict[str, object]:
        async with self.lifecycle_lock:
            try:
                scope = await asyncio.to_thread(get_session_deletion_scope, self._config.sessions_db, session_id)
            except KeyError:
                raise SessionNotFoundError(session_id) from None
            records = await asyncio.to_thread(list_sessions, self._config.sessions_db, include_archived=True)
            blocked: list[dict[str, str]] = []
            for member in scope.session_ids:
                snapshot = self._runtime.session_snapshot(member)
                if snapshot.run_id is not None or self._runtime.is_session_reserved(member):
                    blocked.append({"session_id": member, "code": "session_busy"})
                elif snapshot.queued_count or snapshot.pending_steer_count:
                    blocked.append({"session_id": member, "code": "pending_inputs"})
                try:
                    await self._journal.session_exists(member)
                except (OSError, RuntimeError, ValueError):
                    blocked.append({"session_id": member, "code": "journal_delete_unsafe"})
            blocked.extend({"session_id": fork, "code": "fork_referenced"} for fork in scope.external_fork_ids)
            return {
                "session_id": session_id,
                "sessions": [
                    {"session_id": r.session_id, "title": r.title} for r in records if r.session_id in scope.session_ids
                ],
                "blocked": blocked,
            }

    async def delete(self, session_id: str, *, expected_session_ids: tuple[str, ...] | None = None) -> tuple[str, ...]:
        async with self.lifecycle_lock:
            return await self._delete(session_id, expected_session_ids=expected_session_ids)

    async def _delete(self, session_id: str, *, expected_session_ids: tuple[str, ...] | None) -> tuple[str, ...]:
        async with AsyncExitStack() as reservations:
            try:
                scope = await asyncio.to_thread(get_session_deletion_scope, self._config.sessions_db, session_id)
            except KeyError:
                raise SessionNotFoundError(session_id) from None
            if expected_session_ids is not None and set(expected_session_ids) != set(scope.session_ids):
                raise SessionDeletionScopeChangedError("Preview the changed Spawn descendants and confirm again")
            if scope.external_fork_ids:
                raise SessionHasChildrenError
            for member in scope.session_ids:
                if self._runtime.has_queued_inputs(member):
                    raise SessionHasQueuedInputsError
                await reservations.enter_async_context(self._runtime.reserve_session(member))
            fenced: list[str] = []
            committed = False
            cancel_event = threading.Event()
            commit_lock = threading.Lock()
            try:
                for member in scope.session_ids:
                    try:
                        await self._journal.prepare_session_delete(member)
                    except (RuntimeError, ValueError, OSError) as error:
                        raise SessionDeletionUnsafeError(
                            "Session history is unsafe to delete; check System Status."
                        ) from error
                    fenced.append(member)
                committed, error = await _await_delete_transaction(
                    asyncio.to_thread(
                        delete_session_transaction,
                        self._config.sessions_db,
                        session_id,
                        expected_session_ids=scope.session_ids,
                        cancel_event=cancel_event,
                        commit_lock=commit_lock,
                    ),
                    on_cancel=lambda: _cancel_delete_transaction(cancel_event, commit_lock),
                )
                if not committed:
                    assert error is not None
                    if isinstance(error, SessionExternalForkError):
                        raise SessionHasChildrenError from error
                    raise error
            except BaseException:
                if not committed:
                    for member in fenced:
                        try:
                            await self._journal.cancel_session_delete(member)
                        except BaseException:
                            logger.exception("Failed to release session delete fence %s", member)
                raise
            logger.info("Session deletion committed root=%s session_ids=%s", session_id, scope.session_ids)
            await self._cleanup_deleted_sessions(scope.session_ids)
            if error is not None:
                raise error
            logger.info("Deleted Spawn scope root=%s count=%d", session_id, len(scope.session_ids))
            return scope.session_ids

    async def _cleanup_deleted_sessions(self, session_ids: tuple[str, ...]) -> None:
        failed = False
        for member in session_ids:
            self._runtime.session_models.discard(member)
            try:
                await self._journal.delete_session(member)
            except BaseException:
                failed = True
                logger.exception("Session delete committed but cleanup failed session=%s", member)
            try:
                self._presentation_state.delete(member)
            except BaseException:
                failed = True
                logger.exception("Session delete committed but presentation cleanup failed session=%s", member)
        if failed:
            raise SessionDeletionCleanupError(session_ids)

    async def cleanup_deleted_sessions(self, session_ids: tuple[str, ...]) -> None:
        """Retry post-commit cleanup only for proven absent Session identities."""
        async with self.lifecycle_lock, AsyncExitStack() as reservations:
            for member in sorted(set(session_ids)):
                if await asyncio.to_thread(get_session, self._config.sessions_db, member) is not None:
                    raise SessionBusyError("Cleanup cannot remove history for an existing Session")
                await reservations.enter_async_context(self._runtime.reserve_session(member))
            await self._cleanup_deleted_sessions(session_ids)

    async def compact(self, session_id: str) -> CompactionResult:
        if await asyncio.to_thread(is_session_archived, self._config.sessions_db, session_id):
            raise SessionArchivedError
        try:
            result = await self._runtime.compact_session(session_id)
        except SessionBusyError:
            raise
        except BaseException as exc:
            if session_history_restore_failed(exc):
                logger.exception("Manual context compaction failed and session history restoration was unsuccessful")
                raise SessionCompactionError(consistency_uncertain=True) from exc
            if not isinstance(exc, Exception):
                raise
            logger.exception("Manual context compaction failed; existing context was kept")
            raise SessionCompactionError(consistency_uncertain=False) from exc
        if result.changed:
            logger.info("Context compaction completed: trigger=manual session=%s", session_id)
        return result
