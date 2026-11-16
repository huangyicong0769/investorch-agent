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
