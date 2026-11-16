import json
import wave
from pathlib import Path

import httpx
import pytest

from investorch.llm.deepseek_services import DeepSeekServiceClient, DeepSeekServiceError, input_file
from tests.support.config import make_test_config


@pytest.mark.parametrize("purpose", ["user_data", "text_recognition", "rich_layout_recognition"])
async def test_files_lifecycle_uses_shared_connection_and_document_purpose(tmp_path: Path, purpose: str):
    config = make_test_config(tmp_path, {"models": {"test": {"type": "deepseek"}}})
    sample = tmp_path / ("fixture.txt" if purpose == "user_data" else "fixture.pdf")
    sample.write_bytes(b"fixture")
    info = {
        "id": "file-api-123",
        "object": "file",
        "filename": sample.name,
        "purpose": purpose,
        "bytes": 7,
        "created_at": 100,
        "expires_at": 3700,
        "status": "processed",
    }

    def handle(request):
        assert request.headers["authorization"] == "Bearer fixture-key-never-used-for-network"
        if request.method == "POST":
            assert request.url.path == "/files"
            assert request.content.index(b'name="purpose"') < request.content.index(b'name="file"')
            assert purpose.encode() in request.content
            assert b'name="expires_after[anchor]"' in request.content
            assert b'name="expires_after[seconds]"' in request.content
            return httpx.Response(200, json=info)
        if request.method == "DELETE":
            assert request.url.path == "/files/file-api-123"
            return httpx.Response(200, json={"id": info["id"], "object": "file", "deleted": True})
        if request.url.path == "/files":
            assert dict(request.url.params) == {"purpose": purpose, "limit": "2", "order": "desc"}
            return httpx.Response(200, json={"object": "list", "data": [info], "has_more": False})
        assert request.url.path == "/files/file-api-123"
        return httpx.Response(200, json=info)

    async with DeepSeekServiceClient.from_config(config, "test", transport=httpx.MockTransport(handle)) as client:
        uploaded = await client.upload_file(sample, purpose=purpose)
        assert uploaded.expires_at == 3700 and uploaded.status == "processed"
        assert input_file(uploaded.id) == {"type": "input_file", "file_id": "file-api-123"}
        listing = await client.list_files(purpose=purpose, limit=2, order="desc")
        assert listing.data == [uploaded] and not listing.has_more
        assert await client.get_file(uploaded.id) == uploaded
        assert (await client.delete_file(uploaded.id)).deleted


@pytest.mark.parametrize(
    ("status", "code"), [(400, "parse_failed"), (413, "file_too_large"), (504, "upstream_timeout")]
)
async def test_upload_errors_expose_category_without_payload(tmp_path: Path, status: int, code: str):
    document = tmp_path / "fixture.pdf"
    document.write_bytes(b"fixture")
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            status, headers={"x-request-id": "req-1"}, json={"error": {"message": "provider private data"}}
        )
    )
    async with DeepSeekServiceClient("https://api.deepseek.com/v1", "fixture", transport=transport) as client:
        with pytest.raises(DeepSeekServiceError) as error:
            await client.upload_file(document, purpose="text_recognition")
    assert (error.value.status, error.value.code, error.value.request_id) == (status, code, "req-1")
    assert "provider private data" not in str(error.value)


async def test_ocr_accepts_keepalives_and_retains_raw_page_text(tmp_path: Path):
    sample = tmp_path / "fixture.pdf"
    sample.write_bytes(b"fixture")
    pages = [
        {"index": 0, "finish_reason": "stop", "text": "<|ref|>行<|/ref|><|det|>[[1,2,3,4]]<|/det|>"},
        {"index": 1, "finish_reason": "length", "text": "truncated"},
    ]
    payload = {
        "id": "ocr-1",
        "purpose": "rich_layout_recognition",
        "page_count": 2,
        "pages": pages,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }

    def handle(request):
        assert request.url.path == "/v1/tools/ocr" and request.method == "POST"
        assert b'name="purpose"' in request.content and b"rich_layout_recognition" in request.content
        return httpx.Response(200, content=b"\n\n" + json.dumps(payload).encode())

    async with DeepSeekServiceClient(
        "https://api.deepseek.com", "fixture", transport=httpx.MockTransport(handle)
    ) as client:
        result = await client.ocr(sample, purpose="rich_layout_recognition")
        assert result.page_count == 2
        assert [page.model_dump() for page in result.pages] == pages
        assert result.usage == payload["usage"]


async def test_ocr_distinguishes_error_envelopes_after_starting_http_200(tmp_path: Path):
    sample = tmp_path / "fixture.png"
    sample.write_bytes(b"fixture")
    raw = b'\n\n{"error":{"code":"gateway_timeout_error","message":"private detail"}}'
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=raw))
    async with DeepSeekServiceClient("https://api.deepseek.com", "fixture", transport=transport) as client:
        with pytest.raises(DeepSeekServiceError) as error:
            await client.ocr(sample)
    assert (error.value.code, error.value.status) == ("upstream_timeout", 200)


async def test_asr_uploads_pcm16_and_validates_format_and_duration(tmp_path: Path):
    sample = tmp_path / "fixture.wav"

    def write_wav(frames: int) -> None:
        with wave.open(str(sample), "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x00\x00" * frames)

    def handle(request):
        assert request.url.path == "/v1/tools/asr"
        assert b'name="file"; filename="fixture.wav"' in request.content
        assert b"RIFF" in request.content
        return httpx.Response(200, json={"request_id": "asr-1", "text": "Example", "duration_ms": 1000})

    async with DeepSeekServiceClient(
        "https://api.deepseek.com", "fixture", transport=httpx.MockTransport(handle)
    ) as client:
        write_wav(8000)
        result = await client.transcribe(sample)
        assert (result.request_id, result.text, result.duration_ms) == ("asr-1", "Example", 1000)
        write_wav(8000 * 120 + 1)
        with pytest.raises(DeepSeekServiceError, match="audio_too_long"):
            await client.transcribe(sample)
        sample.write_bytes(b"not an audio file")
        with pytest.raises(DeepSeekServiceError, match="invalid_audio"):
            await client.transcribe(sample)
