from __future__ import annotations

from pathlib import Path

import pytest
from agents import SQLiteSession

from investorch.agents.loop import user_input_to_response_item
from investorch.images import ImageContent, serialize_images
from investorch.inputs import UserInput
from investorch.output import AssistantMessage, ToolOutput
from tests.support.web import open_test_web

IMAGE = ImageContent("data:image/png;base64,iVBORw0KGgo=", filename="chart.png", media_type="image/png")


@pytest.mark.asyncio
async def test_image_api_history_fork_archive_clear_and_delete(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        bootstrap = await web.client.get("/api/bootstrap")
        assert bootstrap.json()["image_config"]["max_image_bytes"] == web.host.config["images.max_image_bytes"]
        assert bootstrap.headers["content-security-policy"] == "img-src 'self' data: https:"
        session_id = (await web.client.post("/api/sessions")).json()["session"]["session_id"]
        body = {"images": serialize_images((IMAGE,))}
        response = await web.client.post(f"/api/sessions/{session_id}/messages", json=body)
        assert response.status_code == 200
        await web.runtime.agent_loop.wait_until_started(session_id)
        assert web.runtime.agent_loop.input_for(session_id).structured_input == UserInput("", (IMAGE,))
        web.runtime.agent_loop.complete(session_id)
        await web.runtime.wait_for_run_ended(session_id)
        # This harness controls runtime timing; populate the real SDK store for lifecycle checks.
        session = SQLiteSession(session_id, web.host.config.sessions_db)
        await session.add_items([user_input_to_response_item(UserInput("", (IMAGE,)))])
        session.close()
        await web.host.journal.record_output(session_id, AssistantMessage("", images=(IMAGE,)))
        await web.host.journal.record_output(session_id, ToolOutput("", images=(IMAGE,)))
        history = (await web.client.get(f"/api/sessions/{session_id}/history")).json()
        assert all(record["images"] == body["images"] for record in history["records"])
        page = (await web.client.get(f"/api/sessions/{session_id}/history?limit=1")).json()
        older = (
            await web.client.get(f"/api/sessions/{session_id}/history?before_seq={page['oldest_seq']}&limit=2")
        ).json()
        assert older["records"] + page["records"] == history["records"]
        fork = await web.client.post(f"/api/sessions/{session_id}/fork")
        assert fork.status_code == 200
        fork_id = fork.json()["session"]["session_id"]
        assert (await web.client.get(f"/api/sessions/{fork_id}/history")).json()["records"] == history["records"]
        clone = SQLiteSession(fork_id, web.host.config.sessions_db)
        assert (await clone.get_items())[1]["content"][0]["image_url"] == IMAGE.image_url
        clone.close()
        for action in ("archive", "unarchive"):
            assert (await web.client.post(f"/api/sessions/{fork_id}/{action}")).status_code == 200
            assert (await web.client.get(f"/api/sessions/{fork_id}/history")).json()["records"] == history["records"]
        deleted = await web.client.request("DELETE", f"/api/sessions/{fork_id}", json={"confirm": True})
        assert deleted.status_code == 200
        assert not await web.host.journal.session_exists(fork_id)
        clear = await web.client.post(f"/api/sessions/{session_id}/clear", json={"confirm": True})
        assert clear.status_code == 200
        replacement = clear.json()["replacement_session_id"]
        assert (await web.client.get(f"/api/sessions/{replacement}/history")).json()["records"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "image",
    [
        {"image_url": "https://example.com/chart.png"},
        {"image_url": "data:image/png;base64,PRIVATE_PAYLOAD_NOT_BASE64"},
        {"image_url": IMAGE.image_url, "extra": "PRIVATE_PAYLOAD_NOT_BASE64"},
        {"image_url": IMAGE.image_url, "detail": "PRIVATE_PAYLOAD_NOT_BASE64"},
    ],
)
async def test_invalid_image_request_never_runs_or_echoes_payload(tmp_path: Path, image: dict) -> None:
    async with open_test_web(tmp_path) as web:
        session_id = (await web.client.post("/api/sessions")).json()["session"]["session_id"]
        response = await web.client.post(f"/api/sessions/{session_id}/messages", json={"images": [image]})
        assert response.status_code in (400, 422)
        assert "PRIVATE_PAYLOAD_NOT_BASE64" not in response.text
        assert IMAGE.image_url not in response.text
        assert not web.runtime.runtime.is_session_active(session_id)
        assert not await web.host.journal.session_exists(session_id)
