"""Shared conversation input, trusted origin, and deterministic input validation."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

from investorch.config import AppConfig
from investorch.images import ACCEPTED_INPUT_MIME_TYPES, ImageContent, detect_image_media_type


@dataclass(frozen=True, slots=True)
class InputOrigin:
    kind: Literal["user", "agent"] = "user"
    sender_session_id: str | None = None
    sender_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("user", "agent"):
            raise ValueError("invalid_input_origin")
        identities = (self.sender_session_id, self.sender_run_id)
        if self.kind == "user":
            if any(value is not None for value in identities):
                raise ValueError("user_origin_cannot_have_sender")
        elif any(not isinstance(value, str) or not value.strip() or not value.isprintable() for value in identities):
            raise ValueError("agent_origin_requires_session_and_run")


@dataclass(frozen=True, slots=True)
class UserInput:
    text: str
    images: tuple[ImageContent, ...] = ()
    origin: InputOrigin = field(default_factory=InputOrigin)

    def __post_init__(self) -> None:
        object.__setattr__(self, "images", tuple(self.images))
        if not self.text.strip() and not self.images:
            raise ValueError("User input must contain text or images")


def input_origin_fields(origin: InputOrigin) -> dict[str, object]:
    """Keep ordinary user payloads unchanged and emit only Agent provenance."""
    return {"origin": asdict(origin)} if origin.kind == "agent" else {}


def input_origin_from_record(record: Mapping[str, object]) -> InputOrigin:
    raw = record.get("origin")
    if raw is None:
        return InputOrigin()
    if not isinstance(raw, Mapping) or set(raw) - {"kind", "sender_session_id", "sender_run_id"}:
        raise ValueError("invalid_input_origin")
    return InputOrigin(
        cast(Literal["user", "agent"], raw.get("kind")),
        cast(str | None, raw.get("sender_session_id")),
        cast(str | None, raw.get("sender_run_id")),
    )


def normalize_user_input(text: str, images: Sequence[Mapping[str, Any]], config: AppConfig) -> UserInput:
    if len(images) > config["images.max_images_per_input"]:
        raise ValueError("too_many_images")
    normalized = []
    total = 0
    for raw in images:
        url = raw["image_url"]
        header, separator, encoded = url.partition(",")
        if not header.startswith("data:") or not separator or not header.endswith(";base64"):
            raise ValueError("invalid_image_data_url")
        mime = header[5:-7]
        if mime not in ACCEPTED_INPUT_MIME_TYPES:
            raise ValueError("unsupported_image_media_type")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid_image_base64") from None
        if not data or detect_image_media_type(data) != mime or raw.get("media_type") not in (None, mime):
            raise ValueError("image_media_type_mismatch")
        if len(data) > config["images.max_image_bytes"]:
            raise ValueError("image_too_large")
        total += len(data)
        if total > config["images.max_total_image_bytes"]:
            raise ValueError("image_total_too_large")
        normalized.append(
            ImageContent(url, raw.get("detail") or config["images.default_detail"], raw.get("filename"), mime)
        )
    return UserInput(text, tuple(normalized))


def input_summary(user_input: UserInput | str) -> str:
    if isinstance(user_input, str):
        return user_input
    marker = f"[{len(user_input.images)} images attached]" if user_input.images else ""
    return "\n".join(part for part in (user_input.text, marker) if part)
