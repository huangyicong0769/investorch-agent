from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import httpx
import pytest

from investorch_qmt.config import load_config
from investorch_qmt.server import create_app

TOKEN = "health-token-with-at-least-32-characters"


def service_config(tmp_path: Path):
    path = tmp_path / "investorch-qmt.toml"
    path.write_text(f"[auth]\ntoken = '{TOKEN}'\n", encoding="utf-8")
    return load_config(path)


@pytest.mark.asyncio
async def test_authenticated_health_reports_only_ready_service_truth(tmp_path: Path) -> None:
    app = create_app(service_config(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "investorch-qmt",
        "version": version("investorch-qmt"),
        "mcp": {"status": "ready"},
    }
    serialized = response.text.lower()
    for forbidden in (TOKEN.lower(), "qmt", "account", "hostname", "username", "config", "environment"):
        assert forbidden not in serialized.replace("investorch-qmt", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "Bearer wrong-token"])
async def test_health_requires_bearer_authentication(tmp_path: Path, authorization: str | None) -> None:
    app = create_app(service_config(tmp_path))
    headers = {"Authorization": authorization} if authorization is not None else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.get("/healthz", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


@pytest.mark.asyncio
async def test_unknown_route_remains_a_normal_404(tmp_path: Path) -> None:
    app = create_app(service_config(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.get("/whatever")

    assert response.status_code == 404
