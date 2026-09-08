import httpx
import pytest
from test_execution_service import stage_body
from test_mcp import TOKEN, service_config

from investorch_qmt.config import default_paths
from investorch_qmt.execution.service import ExecutionNodeService
from investorch_qmt.server import create_app


@pytest.mark.asyncio
async def test_rest_uses_same_auth_host_and_service_boundary(tmp_path):
    paths = default_paths(tmp_path)
    service = ExecutionNodeService(paths)
    app = create_app(service_config(tmp_path), paths=paths, service=service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765") as client:
        assert (await client.get("/api/v1/node/status")).status_code == 401
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        assert (await client.get("/api/v1/node/status", headers={"Host": "evil.example"})).status_code == 421
        opened = (await client.post("/api/v1/control-sessions")).json()
        client.headers["X-InvestOrch-Control-Session"] = opened["session_id"]
        response = await client.put("/api/v1/deployments/deployment-a", json=stage_body())
        assert response.status_code == 200
        status = (await client.get("/api/v1/node/status")).json()
        assert status == service.get_node_status()
        assert status["deployments"][0]["status"] == "STAGED"
        assert (await client.get("/api/v1/facts/next")).json() == {"fact": None}


@pytest.mark.asyncio
async def test_rest_rejects_malformed_exact_requests_and_stale_sessions(tmp_path, caplog):
    service = ExecutionNodeService(default_paths(tmp_path))
    app = create_app(service_config(tmp_path), service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        first = (await client.post("/api/v1/control-sessions")).json()["session_id"]
        second = (await client.post("/api/v1/control-sessions")).json()["session_id"]
        client.headers["X-InvestOrch-Control-Session"] = first
        rejected = await client.put("/api/v1/deployments/deployment-a", json=stage_body())
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "STALE_CONTROL_SESSION"
        client.headers["X-InvestOrch-Control-Session"] = second
        for content in (
            '{"extra":1}',
            '{"committed_ledger_sequence":true}',
            '{"committed_ledger_sequence":1,"committed_ledger_sequence":2}',
            "[]",
            "NaN",
        ):
            rejected = await client.post("/api/v1/facts/unknown/ack", content=content)
            assert rejected.status_code == 400
            assert set(rejected.json()) == {"code", "message", "retryable"}
        assert (await client.get("/api/v1/facts/next?limit=10")).status_code == 400
        await client.put("/api/v1/deployments/deployment-a", json=stage_body(source=b"SECRET STRATEGY"))
        await client.get("/api/v1/node/status", headers={"Authorization": "Bearer wrong-secret"})
    assert TOKEN not in caplog.text
    assert "wrong-secret" not in caplog.text
    assert "SECRET STRATEGY" not in caplog.text
    assert "U0VDUkVUIFNUUkFURUdZ" not in caplog.text
