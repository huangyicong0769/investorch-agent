from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path

from agents import SQLiteSession

from investorch.journal import SessionJournal

from .sessions import (
    create_session,
    delete_session_metadata,
    get_session_branch_from,
    get_session_title,
    session_exists,
    set_session_branch_from,
    set_session_title,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionForkResult:
    source_session_id: str
    target_session_id: str
    title: str | None


class SessionForkError(RuntimeError):
    pass


class SessionForkRollbackError(SessionForkError):
    def __init__(
        self,
        target_session_id: str,
        original_error: BaseException,
        rollback_errors: list[BaseException],
    ) -> None:
        super().__init__(f"Session fork failed and rollback was incomplete for target {target_session_id}")
        self.target_session_id = target_session_id
        self.original_error = original_error
        self.rollback_errors = tuple(rollback_errors)


async def fork_session(
    *,
    source_session_id: str,
    target_session_id: str,
    sessions_db: Path,
    journal: SessionJournal,
) -> SessionForkResult:
    if source_session_id == target_session_id:
        raise SessionForkError("Source and target session IDs must be different")

    await _ensure_target_available(target_session_id, sessions_db, journal)

    source: SQLiteSession | None = None
    target: SQLiteSession | None = None
    handles_closed = False
    target_cleanup_required = False

    try:
        source = SQLiteSession(source_session_id, sessions_db)
        source_items = await source.get_items()
        source_title = await asyncio.to_thread(
            get_session_title,
            sessions_db,
            source_session_id,
        )

        target_cleanup_required = True
        await _await_mutation(asyncio.to_thread(create_session, sessions_db, target_session_id))
        target = SQLiteSession(target_session_id, sessions_db)
        await target.add_items(source_items)
        await journal.clone_session(source_session_id, target_session_id)

        title = _fork_title(source_title)
        if title is not None:
            await _await_mutation(
                asyncio.to_thread(
                    set_session_title,
                    sessions_db,
                    target_session_id,
                    title,
                )
            )
        await _await_mutation(
            asyncio.to_thread(
                set_session_branch_from,
                sessions_db,
                target_session_id,
                source_session_id,
            )
        )

        close_errors = _close_session_handles(target, source)
        handles_closed = True
        if close_errors:
            raise close_errors[0]
    except BaseException as original_error:
        cleanup_errors: list[BaseException] = []
        if not handles_closed:
            cleanup_errors.extend(_close_session_handles(target, source))
        if target_cleanup_required:
            cleanup_errors.extend(await _rollback_target(target_session_id, sessions_db, journal))

        if isinstance(original_error, asyncio.CancelledError):
            logger.info(
                "Cancelled session fork source=%s target=%s",
                source_session_id,
                target_session_id,
            )
        else:
            logger.error(
                "Failed to fork session source=%s target=%s",
                source_session_id,
                target_session_id,
                exc_info=(
                    type(original_error),
                    original_error,
                    original_error.__traceback__,
                ),
            )

        if cleanup_errors:
            for cleanup_error in cleanup_errors:
                logger.error(
                    "Failed to roll back fork target=%s",
                    target_session_id,
                    exc_info=(
                        type(cleanup_error),
                        cleanup_error,
                        cleanup_error.__traceback__,
                    ),
                )
            raise SessionForkRollbackError(
                target_session_id,
                original_error,
                cleanup_errors,
            ) from original_error

        if isinstance(original_error, asyncio.CancelledError):
            raise
        raise SessionForkError(f"Failed to fork session {source_session_id} to {target_session_id}") from original_error

    logger.info(
        "Forked session source=%s target=%s",
        source_session_id,
        target_session_id,
    )
    return SessionForkResult(
        source_session_id=source_session_id,
        target_session_id=target_session_id,
        title=title,
    )


def _fork_title(source_title: str | None) -> str | None:
    if source_title is None or not source_title.strip():
        return None
    return f"{source_title} (fork)"


async def _await_mutation[T](awaitable: Awaitable[T]) -> T:
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error

    try:
        result = task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation from None
        raise
    if cancellation is not None:
        raise cancellation from None
    return result


async def _ensure_target_available(
    target_session_id: str,
    sessions_db: Path,
    journal: SessionJournal,
) -> None:
    identity_exists, title, branch_from = await asyncio.gather(
        asyncio.to_thread(session_exists, sessions_db, target_session_id),
        asyncio.to_thread(get_session_title, sessions_db, target_session_id),
        asyncio.to_thread(get_session_branch_from, sessions_db, target_session_id),
    )
    journal_exists = await journal.session_exists(target_session_id)
    if identity_exists or title is not None or branch_from is not None or journal_exists:
        raise SessionForkError(f"Target session already exists: {target_session_id}")


def _close_session_handles(
    *sessions: SQLiteSession | None,
) -> list[BaseException]:
    errors: list[BaseException] = []
    for session in sessions:
        if session is None:
            continue
        try:
            session.close()
        except BaseException as error:
            errors.append(error)
    return errors


async def _rollback_target(
    target_session_id: str,
    sessions_db: Path,
    journal: SessionJournal,
) -> list[BaseException]:
    task = asyncio.create_task(_rollback_target_unshielded(target_session_id, sessions_db, journal))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        result = await asyncio.gather(task, return_exceptions=True)
        if isinstance(result[0], list):
            return result[0]
        return [result[0]]


async def _rollback_target_unshielded(
    target_session_id: str,
    sessions_db: Path,
    journal: SessionJournal,
) -> list[BaseException]:
    errors: list[BaseException] = []
    target: SQLiteSession | None = None
    try:
        target = SQLiteSession(target_session_id, sessions_db)
        await target.clear_session()
    except BaseException as error:
        errors.append(error)
    finally:
        errors.extend(_close_session_handles(target))

    try:
        await journal.delete_session(target_session_id)
    except BaseException as error:
        errors.append(error)

    try:
        await asyncio.to_thread(
            delete_session_metadata,
            sessions_db,
            target_session_id,
        )
    except BaseException as error:
        errors.append(error)

    return errors
