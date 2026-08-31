from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable

from agents import SQLiteSession

from qmt_agent.agents import CompactionResult, session_history_restore_failed
from qmt_agent.config import AppConfig
from qmt_agent.journal import SessionJournal
from qmt_agent.runtime import AgentRuntime, SessionBusyError
from qmt_agent.storage import (
    archive_session,
    create_session,
    delete_session_metadata,
    delete_session_transaction,
    delete_unused_session,
    fork_session,
    is_session_archived,
    session_has_children,
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


class SessionHasQueuedInputsError(RuntimeError):
    pass


class SessionHasChildrenError(RuntimeError):
    pass


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

    async def create(self) -> str:
        session_id = uuid.uuid4().hex
        await asyncio.to_thread(create_session, self._config.sessions_db, session_id)
        logger.info("Started session %s", session_id)
        return session_id

    async def discard_if_unused(self, session_id: str) -> bool:
        if self._runtime.has_queued_inputs(session_id):
            return False
        try:
            async with self._runtime.reserve_session(session_id):
                if await self._journal.session_exists(session_id):
                    return False
                deleted = await asyncio.to_thread(delete_unused_session, self._config.sessions_db, session_id)
        except SessionBusyError:
            return False
        except Exception:
            logger.exception("Failed to discard unused session %s", session_id)
            return False
        if deleted:
            logger.info("Discarded unused session %s", session_id)
        return deleted

    async def archive(self, session_id: str) -> None:
        if self._runtime.has_queued_inputs(session_id):
            raise SessionHasQueuedInputsError
        async with self._runtime.reserve_session(session_id):
            if await asyncio.to_thread(is_session_archived, self._config.sessions_db, session_id):
                raise SessionAlreadyArchivedError
            await asyncio.to_thread(archive_session, self._config.sessions_db, session_id)
        logger.info("Session archived session=%s", session_id)

    async def unarchive(self, session_id: str) -> None:
        await asyncio.to_thread(unarchive_session, self._config.sessions_db, session_id)
        logger.info("Session unarchived session=%s", session_id)

    async def fork(self, source_session_id: str) -> str:
        if self._runtime.has_queued_inputs(source_session_id):
            raise SessionHasQueuedInputsError
        target_session_id = uuid.uuid4().hex
        while target_session_id == source_session_id:
            target_session_id = uuid.uuid4().hex
        async with self._runtime.reserve_session(source_session_id):
            async with self._runtime.reserve_session(target_session_id):
                await fork_session(
                    source_session_id=source_session_id,
                    target_session_id=target_session_id,
                    sessions_db=self._config.sessions_db,
                    journal=self._journal,
                )
        return target_session_id

    async def set_title(self, session_id: str, title: str) -> None:
        if await asyncio.to_thread(is_session_archived, self._config.sessions_db, session_id):
            raise SessionArchivedError
        await asyncio.to_thread(set_session_title, self._config.sessions_db, session_id, title)
        logger.info("Updated title for session %s", session_id)

    async def clear(self, session_id: str) -> str:
        if await asyncio.to_thread(is_session_archived, self._config.sessions_db, session_id):
            raise SessionArchivedError
        if self._runtime.has_queued_inputs(session_id):
            raise SessionHasQueuedInputsError
        async with self._runtime.reserve_session(session_id):
            session = SQLiteSession(session_id, self._config.sessions_db)
            try:
                await session.clear_session()
            finally:
                session.close()
            await asyncio.to_thread(delete_session_metadata, self._config.sessions_db, session_id)
            replacement_session_id = await self.create()
        logger.info("Cleared session %s and started session %s", session_id, replacement_session_id)
        return replacement_session_id

    async def delete(self, session_id: str) -> None:
        if self._runtime.has_queued_inputs(session_id):
            raise SessionHasQueuedInputsError
        async with self._runtime.reserve_session(session_id):
            if await asyncio.to_thread(session_has_children, self._config.sessions_db, session_id):
                raise SessionHasChildrenError
            await self._journal.prepare_session_delete(session_id)
            committed = False
            cancel_event = threading.Event()
            commit_lock = threading.Lock()
            try:
                committed, error = await _await_delete_transaction(
                    asyncio.to_thread(
                        delete_session_transaction,
                        self._config.sessions_db,
                        session_id,
                        cancel_event=cancel_event,
                        commit_lock=commit_lock,
                    ),
                    on_cancel=lambda: _cancel_delete_transaction(cancel_event, commit_lock),
                )
                if not committed:
                    assert error is not None
                    raise error
            except BaseException:
                if not committed:
                    try:
                        await self._journal.cancel_session_delete(session_id)
                    except BaseException:
                        logger.exception("Failed to release session delete fence %s", session_id)
                raise
            try:
                await self._journal.delete_session(session_id)
            except BaseException:
                logger.exception("Session delete committed but journal cleanup failed session=%s", session_id)
            try:
                self._presentation_state.delete(session_id)
            except BaseException:
                logger.exception("Session delete committed but presentation cleanup failed session=%s", session_id)
        logger.info("Deleted session %s", session_id)

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
