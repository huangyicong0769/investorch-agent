"""Image conversation content, serialization, and media detection."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from investorch.inputs import UserInput

ImageDetail = Literal["auto", "low", "high", "original"]
IMAGE_DETAILS = ("auto", "low", "high", "original")
ACCEPTED_INPUT_MIME_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
RENDERABLE_MIME_TYPES = (*ACCEPTED_INPUT_MIME_TYPES, "image/svg+xml")


@dataclass(frozen=True, slots=True)
class ImageContent:
    image_url: str = field(repr=False)
    detail: ImageDetail = "auto"
    filename: str | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        if self.detail not in IMAGE_DETAILS:
            raise ValueError("invalid_image_detail")
        if self.image_url.startswith("data:"):
            header, separator, _ = self.image_url.partition(",")
            if not separator or header.removeprefix("data:").removesuffix(";base64") not in RENDERABLE_MIME_TYPES:
                raise ValueError("unsupported_image_media_type")
        else:
            try:
                url = urlsplit(self.image_url)
                valid = url.scheme == "https" and bool(url.hostname)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("unsupported_image_url_scheme")


def detect_image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def serialize_images(images: Sequence[ImageContent]) -> list[dict[str, Any]]:
    return [asdict(image) for image in images]


def user_input_to_response_item(user_input: UserInput) -> dict[str, Any]:
    content = []
    if user_input.origin.kind == "agent":
        # Provenance accompanies ordinary user content; it never gains system/developer authority.
        session_id = json.dumps(user_input.origin.sender_session_id, ensure_ascii=False)
        run_id = json.dumps(user_input.origin.sender_run_id, ensure_ascii=False)
        content.append(
            {"type": "input_text", "text": f"Message sent by another InvestOrch Session: {session_id} (Run {run_id})."}
        )
    if user_input.text.strip():
        content.append({"type": "input_text", "text": user_input.text})
    content.extend(
        {"type": "input_image", "image_url": image.image_url, "detail": image.detail} for image in user_input.images
    )
    return {"role": "user", "content": content}


def image_summary(image: ImageContent) -> str:
    if image.image_url.startswith("https:"):
        return f"[external image: {urlsplit(image.image_url).hostname}]"
    return f"[image: {image.filename or image.media_type or 'attached image'}]"
