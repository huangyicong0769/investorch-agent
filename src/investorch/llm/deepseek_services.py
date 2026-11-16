"""Direct DeepSeek service APIs using the configured API-key connection."""

import os
import re
from pathlib import Path
from typing import Any, Literal, Self, get_args

import httpx
from openai.types.responses import ResponseInputFileParam
from pydantic import BaseModel, ConfigDict, ValidationError

from investorch.config import AppConfig, ConfigError
from investorch.images import detect_image_media_type

FilePurpose = Literal["user_data", "text_recognition", "rich_layout_recognition"]
OCRPurpose = Literal["text_recognition", "rich_layout_recognition"]
_MIB = 1024 * 1024
_ERROR_BY_STATUS = {
    401: "authentication_failed",
    402: "insufficient_balance",
    403: "access_denied",
    404: "not_found_or_expired",
    408: "upload_timeout",
    413: "file_too_large",
    429: "rate_limit",
    503: "service_busy",
    504: "upstream_timeout",
}


class DeepSeekServiceError(RuntimeError):
    def __init__(
        self,
        operation: str,
        code: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.request_id = request_id
        self.usage = usage
        detail = f"{code}, HTTP {status}" if status is not None else code
        super().__init__(f"DeepSeek {operation} failed ({detail}).")


class _ServiceResult(BaseModel):
    model_config = ConfigDict(extra="allow")


class FileInfo(_ServiceResult):
    id: str
    filename: str
    purpose: str
    bytes: int
    created_at: int
    expires_at: int | None = None
    status: str | None = None


class FilePage(_ServiceResult):
    data: list[FileInfo]
    has_more: bool = False
    first_id: str | None = None
    last_id: str | None = None


class FileDeletion(_ServiceResult):
    id: str
    deleted: bool


class OCRPage(_ServiceResult):
    index: int
    text: str
    finish_reason: str


class OCRResult(_ServiceResult):
    id: str
    purpose: str
    page_count: int
    pages: list[OCRPage]
    usage: dict[str, Any] | None = None


def _file_id(value: str) -> str:
    if not re.fullmatch(r"file-api-[a-zA-Z0-9_-]+", value):
        raise ValueError("Expected a DeepSeek file ID (file-api-...).")
    return value


def input_file(file_id: str) -> ResponseInputFileParam:
    return {"type": "input_file", "file_id": _file_id(file_id)}


class DeepSeekServiceClient:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/").removesuffix("/v1") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
            transport=transport,
        )

    @classmethod
    def from_config(
        cls, config: AppConfig, connection_id: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> Self:
        connection = config.model_connection(connection_id)
        if not connection.is_deepseek:
            raise ConfigError("Choose a DeepSeek API-key connection for DeepSeek services.")
        return cls(connection.base_url, config.secret(connection.api_key_secret), transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _error(
        operation: str, response: httpx.Response, payload: object = None, *, invalid: str = "invalid_request"
    ) -> DeepSeekServiceError:
        body = payload if isinstance(payload, dict) else {}
        status = response.status_code
        code = invalid if status == 400 else _ERROR_BY_STATUS.get(status, "service_error")
        detail = body.get("error")
        if isinstance(detail, dict):
            problem = str(detail.get("message", "")).casefold()
            if "quota" in problem or "storage limit" in problem:
                code = "quota_exceeded"
            elif "content exists risk" in problem:
                code = "content_filter"
            elif detail.get("code") == "gateway_timeout_error":
                code = "upstream_timeout"
        request_id = response.headers.get("x-request-id") or body.get("request_id")
        usage = body.get("usage")
        return DeepSeekServiceError(
            operation,
            code,
            status=status,
            request_id=request_id if isinstance(request_id, str) else None,
            usage=usage if isinstance(usage, dict) else None,
        )

    async def _json[T: BaseModel](
        self,
        operation: str,
        schema: type[T],
        method: str,
        path: str,
        *,
        invalid: str = "invalid_request",
        **kwargs: Any,
    ) -> T:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise DeepSeekServiceError(operation, "timeout") from None
        except httpx.RequestError:
            raise DeepSeekServiceError(operation, "connection_failed") from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not response.is_success or (isinstance(payload, dict) and payload.get("error") is not None):
            raise self._error(operation, response, payload, invalid=invalid)
        try:
            return schema.model_validate(payload)
        except ValidationError:
            raise DeepSeekServiceError(
                operation,
                "invalid_response",
                status=response.status_code,
                request_id=response.headers.get("x-request-id"),
            ) from None

    async def upload_file(
        self, path: str | Path, *, purpose: FilePurpose = "user_data", expires_after: int | None = 3600
    ) -> FileInfo:
        if purpose not in get_args(FilePurpose):
            raise ValueError("Unsupported DeepSeek file purpose.")
        form = {"purpose": purpose}
        if expires_after is not None:
            if not 3600 <= expires_after <= 2592000:
                raise ValueError("File lifetime must be between 3600 and 2592000 seconds.")
            form.update({"expires_after[anchor]": "created_at", "expires_after[seconds]": str(expires_after)})
        path = Path(path)
        if len(path.name.encode()) > 512:
            raise ValueError("DeepSeek filenames may contain at most 512 bytes.")
        parsing = purpose != "user_data"
        with path.open("rb") as file:
            image = detect_image_media_type(file.read(16)) is not None
            file.seek(0)
            limit = 100 if parsing else 64 if image else 32
            if os.fstat(file.fileno()).st_size > limit * _MIB:
                raise DeepSeekServiceError("file upload", "file_too_large")
            timeout = httpx.Timeout(150 if parsing else 600, connect=30, write=120 if parsing else 600)
            return await self._json(
                "file upload",
                FileInfo,
                "POST",
                "files",
                data=form,
                files={"file": (path.name, file)},
                timeout=timeout,
                invalid="parse_failed" if parsing else "invalid_request",
            )

    async def list_files(
        self,
        *,
        after: str | None = None,
        limit: int = 1000,
        order: Literal["asc", "desc"] = "asc",
        purpose: FilePurpose | None = None,
    ) -> FilePage:
        if not 1 <= limit <= 1000 or order not in {"asc", "desc"}:
            raise ValueError("Invalid DeepSeek file pagination settings.")
        params = {"limit": str(limit), "order": order}
        if after is not None:
            params["after"] = _file_id(after)
        if purpose is not None:
            params["purpose"] = purpose
        return await self._json("file list", FilePage, "GET", "files", params=params)

    async def get_file(self, file_id: str) -> FileInfo:
        return await self._json("file lookup", FileInfo, "GET", f"files/{_file_id(file_id)}")

    async def delete_file(self, file_id: str) -> FileDeletion:
        return await self._json("file deletion", FileDeletion, "DELETE", f"files/{_file_id(file_id)}")

    async def ocr(self, path: str | Path, *, purpose: OCRPurpose = "text_recognition") -> OCRResult:
        if purpose not in get_args(OCRPurpose):
            raise ValueError("Unsupported DeepSeek OCR purpose.")
        path = Path(path)
        with path.open("rb") as file:
            if os.fstat(file.fileno()).st_size > 100 * _MIB:
                raise DeepSeekServiceError("OCR", "file_too_large")
            return await self._json(
                "OCR",
                OCRResult,
                "POST",
                "v1/tools/ocr",
                data={"purpose": purpose},
                files={"file": (path.name, file)},
                timeout=httpx.Timeout(185, connect=30, write=30),
                invalid="recognition_failed",
            )
