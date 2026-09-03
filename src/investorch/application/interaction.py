import asyncio
from dataclasses import dataclass
from typing import Literal

from investorch.context import AppState
from investorch.runtime import AgentRuntime, RunOptions, SessionBusyError
from investorch.storage import is_session_archived

InputDisposition = Literal["run_started", "steer_submitted", "queue_submitted"]


@dataclass(frozen=True, slots=True)
class UserInputSubmission:
    session_id: str
    disposition: InputDisposition
    run_id: str
    follow_up_id: str | None = None


class UserInputRejected(RuntimeError):
    pass


class ArchivedSessionInputError(UserInputRejected):
    pass


class SteerPromotionPendingError(UserInputRejected):
    pass


class QueuedFollowUpsPendingError(UserInputRejected):
    def __init__(self, *, paused: bool) -> None:
        self.paused = paused


class ActiveRunChangedError(UserInputRejected):
    def __init__(self, *, follow_up: bool) -> None:
        self.follow_up = follow_up


class FollowUpSubmissionError(UserInputRejected):
    pass


def current_run_options(state: AppState) -> RunOptions:
    return RunOptions(
        reasoning_effort=state.main_reasoning_effort,
        permission_mode=state.permission_mode,
        follow_up_behavior=state.follow_up_behavior,
    )


async def submit_user_input(
    *,
    state: AppState,
    runtime: AgentRuntime,
    session_id: str,
    text: str,
) -> UserInputSubmission:
    if not text.strip():
        raise ValueError("User input must not be empty")
    if await asyncio.to_thread(is_session_archived, state.config.sessions_db, session_id):
        raise ArchivedSessionInputError

    if runtime.is_session_active(session_id):
        try:
            submission = await runtime.submit_follow_up(session_id, text, current_run_options(state))
        except SessionBusyError as exc:
            raise ActiveRunChangedError(follow_up=True) from exc
        except Exception as exc:
            raise FollowUpSubmissionError from exc
        return UserInputSubmission(
            session_id=session_id,
            disposition="queue_submitted" if submission.behavior == "queue" else "steer_submitted",
            run_id=submission.active_run_id,
            follow_up_id=submission.follow_up_id,
        )

    snapshot = runtime.session_snapshot(session_id)
    if snapshot.pending_steer_count:
        raise SteerPromotionPendingError
    if runtime.has_queued_inputs(session_id):
        raise QueuedFollowUpsPendingError(paused=snapshot.queue_paused)

    try:
        active_run = runtime.start_run(session_id, text, current_run_options(state))
    except SessionBusyError as exc:
        raise ActiveRunChangedError(follow_up=False) from exc
    return UserInputSubmission(session_id=session_id, disposition="run_started", run_id=active_run.run_id)
