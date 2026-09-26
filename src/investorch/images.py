"""Image conversation content and deterministic input validation."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from investorch.config import AppConfig

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


@dataclass(frozen=True, slots=True)
class UserInput:
    text: str
    images: tuple[ImageContent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "images", tuple(self.images))
        if not self.text.strip() and not self.images:
            raise ValueError("User input must contain text or images")


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


def serialize_images(images: Sequence[ImageContent]) -> list[dict[str, Any]]:
    return [asdict(image) for image in images]


def user_input_to_response_item(user_input: UserInput) -> dict[str, Any]:
    content = []
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


def input_summary(user_input: UserInput | str) -> str:
    if isinstance(user_input, str):
        return user_input
    marker = f"[{len(user_input.images)} images attached]" if user_input.images else ""
    return "\n".join(part for part in (user_input.text, marker) if part)
