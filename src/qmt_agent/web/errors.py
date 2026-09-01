from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from qmt_agent.application import (
    ActiveRunChangedError,
    ArchivedSessionInputError,
    FollowUpSubmissionError,
    QueuedFollowUpsPendingError,
    SessionAlreadyArchivedError,
    SessionArchivedError,
    SessionCompactionError,
    SessionHasChildrenError,
    SessionHasQueuedInputsError,
    SessionNotFoundError,
    SteerPromotionPendingError,
)
from qmt_agent.runtime import SessionBusyError
from qmt_agent.storage import SessionForkError, SessionForkRollbackError

logger = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def raise_application_error(error: Exception) -> NoReturn:
    if isinstance(error, SessionNotFoundError):
        raise APIError(
            404,
            "session_not_found",
            "The requested session does not exist.",
            details={"session_id": error.session_id},
        ) from error
    if isinstance(error, ArchivedSessionInputError | SessionArchivedError):
        raise APIError(409, "session_archived", "This session is archived and read-only.") from error
    if isinstance(error, SessionHasQueuedInputsError):
        raise APIError(409, "session_has_queued_inputs", "This session has queued follow-ups.") from error
    if isinstance(error, SessionHasChildrenError):
        raise APIError(409, "session_has_children", "Delete this session's branches first.") from error
    if isinstance(error, SessionAlreadyArchivedError):
        raise APIError(409, "session_already_archived", "This session is already archived.") from error
    if isinstance(error, QueuedFollowUpsPendingError):
        raise APIError(
            409,
            "queued_followups_pending",
            "This session has queued follow-ups that must finish or be cleared first.",
            details={"paused": error.paused},
        ) from error
    if isinstance(error, SteerPromotionPendingError):
        raise APIError(
            409, "steer_promotion_pending", "A Steer follow-up is being promoted for this session."
        ) from error
    if isinstance(error, ActiveRunChangedError):
        raise APIError(
            409,
            "active_run_changed",
            "The active Run changed before this input could be submitted.",
            details={"follow_up": error.follow_up},
        ) from error
    if isinstance(error, FollowUpSubmissionError):
        raise APIError(503, "follow_up_submission_failed", "The follow-up could not be saved or submitted.") from error
    if isinstance(error, SessionCompactionError):
        raise APIError(
            503,
            "compaction_failed",
            "Session context compaction failed.",
            details={"consistency_uncertain": error.consistency_uncertain},
        ) from error
    if isinstance(error, SessionForkRollbackError):
        raise APIError(
            503,
            "fork_failed",
            "The session could not be forked and cleanup was incomplete.",
            details={"consistency_uncertain": True},
        ) from error
    if isinstance(error, SessionForkError):
        raise APIError(
            503, "fork_failed", "The session could not be forked.", details={"consistency_uncertain": False}
        ) from error
    if isinstance(error, SessionBusyError):
        raise APIError(409, "session_busy", "This session has an active operation.") from error
    raise error


def _error_payload(code: str, message: str, details: dict[str, object] | None = None) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def handle_api_error(_request: Request, error: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code, content=_error_payload(error.code, error.message, error.details)
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        errors = [
            {"type": item["type"], "location": list(item["loc"]), "message": item["msg"]} for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                "validation_error", "The request did not match the required schema.", {"errors": errors}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        message = error.detail if isinstance(error.detail, str) else "The request could not be completed."
        code = "not_found" if error.status_code == 404 else "http_error"
        return JSONResponse(status_code=error.status_code, content=_error_payload(code, message))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception(
            "Unexpected Web API failure method=%s path=%s", request.method, request.url.path, exc_info=error
        )
        return JSONResponse(
            status_code=500, content=_error_payload("internal_error", "An unexpected internal error occurred.")
        )
