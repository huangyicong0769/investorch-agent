from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investorch.context import AppState
from investorch.images import ImageDetail, InputOrigin, detect_image_media_type, normalize_user_input
from investorch.journal import SessionJournal
from investorch.journal.reader import JournalInvalidError, JournalSnapshot, JournalUnavailableError
from investorch.journal.semantics import interpret_steers
from investorch.runtime import AgentRuntime
from investorch.storage import get_session
from investorch.workspace_paths import resolve_workspace_path

from .interaction import (
    ActiveRunChangedError,
    ArchivedSessionInputError,
    FollowUpSubmissionError,
    MissingSessionInputError,
    QueuedFollowUpsPendingError,
    SteerPromotionPendingError,
    UserInputSubmission,
    submit_user_input,
)


class MessageSendError(RuntimeError):
    """A corrective, payload-free message submission failure."""


class MessageImageReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["workspace", "session_event"]
    path: str | None = None
    seq: int | None = Field(default=None, ge=1)
    image_index: int | None = Field(default=None, ge=0)
    detail: ImageDetail | None = None

    @model_validator(mode="after")
    def check_source_fields(self) -> MessageImageReference:
        if self.source == "workspace":
            if not self.path or self.seq is not None or self.image_index is not None:
                raise ValueError("workspace images require path only")
        elif self.path is not None or self.seq is None or self.image_index is None:
            raise ValueError("session_event images require seq and image_index only")
        return self


async def send_message(
    *,
    state: AppState,
    runtime: AgentRuntime,
    journal: SessionJournal,
    source_session_id: str,
    source_run_id: str,
    target_session_id: str,
    text: str = "",
    images: Sequence[MessageImageReference] = (),
) -> UserInputSubmission:
    """Resolve approved input and submit it through the ordinary Run/Steer/Queue entrypoint."""
    if target_session_id == source_session_id:
        raise MessageSendError("self_message: Sending to your own Session is not allowed.")
    if (
        not target_session_id
        or target_session_id in {".", ".."}
        or any(character in target_session_id for character in ("/", "\\", "\0"))
        or Path(target_session_id).name != target_session_id
    ):
        raise MessageSendError("invalid_target: Use a complete, exact Session ID from list_sessions.")
    target = await asyncio.to_thread(get_session, state.config.sessions_db, target_session_id)
    if target is None:
        raise MessageSendError("session_not_found: The exact target Session ID does not exist in this Root.")
    if target.archived_at is not None:
        raise MessageSendError("session_archived: The target Session is archived.")
    if len(images) > state.config["images.max_images_per_input"]:
        raise ValueError("too_many_images")

    prepared = []
    for reference in images:
        if reference.source == "workspace":
            image = await asyncio.to_thread(_workspace_image, state, reference)
        else:
            try:
                image = await journal.read_snapshot(source_session_id, partial(_session_image, reference=reference))
            except (JournalInvalidError, JournalUnavailableError, OSError, ValueError) as exc:
                raise MessageSendError(
                    "image_unavailable: The sender's committed image history cannot be read."
                ) from exc
        if reference.detail is not None:
            image["detail"] = reference.detail
        prepared.append(image)
    user_input = normalize_user_input(text, prepared, state.config)
    user_input = replace(user_input, origin=InputOrigin("agent", source_session_id, source_run_id))
    try:
        return await submit_user_input(
            state=state,
            runtime=runtime,
            session_id=target_session_id,
            user_input=user_input,
            require_existing_session=True,
        )
    except MissingSessionInputError as exc:
        raise MessageSendError("session_not_found: The target no longer exists in this Root.") from exc
    except ArchivedSessionInputError as exc:
        raise MessageSendError("session_archived: The target Session is archived.") from exc
    except QueuedFollowUpsPendingError as exc:
        code = "queue_paused" if exc.paused else "queue_pending"
        raise MessageSendError(f"{code}: Resolve the target's accepted Queue before submitting another input.") from exc
    except SteerPromotionPendingError as exc:
        raise MessageSendError(
            "steer_pending: The target has pending Steer promotion; check its status before retrying."
        ) from exc
    except ActiveRunChangedError as exc:
        raise MessageSendError(
            "target_busy: The target Run changed, is stopping, or is reserved; check its status."
        ) from exc
    except FollowUpSubmissionError as exc:
        raise MessageSendError(
            "input_rejected: The target could not accept the follow-up; check System Status."
        ) from exc
    except RuntimeError as exc:
        if str(exc) == "Agent runtime is closed":
            raise MessageSendError("runtime_closed: This Host is shutting down and cannot accept inputs.") from exc
        raise


def _workspace_image(state: AppState, reference: MessageImageReference) -> dict[str, object]:
    try:
        assert reference.path is not None
        path = resolve_workspace_path(state.config.workspace_dir, reference.path)
        if not path.is_file():
            raise MessageSendError("image_not_found: Workspace image must be an existing regular file.")
        with path.open("rb") as file:
            data = file.read(state.config["images.max_image_bytes"] + 1)
    except (OSError, ValueError) as exc:
        raise MessageSendError("image_path_invalid: Use an accessible file inside the Workspace.") from exc
    if len(data) > state.config["images.max_image_bytes"]:
        raise ValueError("image_too_large")
    mime = detect_image_media_type(data) or "application/octet-stream"
    return {
        "image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}",
        "filename": path.name,
        "media_type": mime,
    }


def _session_image(snapshot: JournalSnapshot, reference: MessageImageReference) -> dict[str, object]:
    facts = interpret_steers(snapshot.iter_records())
    record = next((record for record in snapshot.iter_records() if record["seq"] == reference.seq), None)
    if record is None or record["type"] not in {"user_message", "user_steer", "assistant_message", "tool_output"}:
        raise MessageSendError("image_not_found: The sender's referenced image event does not exist.")
    if record["type"] == "user_steer" and record["seq"] not in facts.activated:
        raise MessageSendError("image_not_effective: The referenced Steer has not been activated.")
    images = record.get("images")
    assert reference.image_index is not None
    if not isinstance(images, list) or reference.image_index >= len(images):
        raise MessageSendError("image_not_found: The referenced image index does not exist.")
    image = images[reference.image_index]
    if not isinstance(image, dict) or not isinstance(image.get("image_url"), str):
        raise MessageSendError("image_invalid: The persisted image has no usable content.")
    return dict(image)
