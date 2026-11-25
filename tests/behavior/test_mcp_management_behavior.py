from __future__ import annotations

from pathlib import Path

import pytest

from investorch.mcp import (
    configure_mcp_server_config,
    read_mcp_server_configs,
)
from tests.support.web import open_test_web


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"enabled": False}, {"timeout": 24}, {"timeout": None}])
async def test_mcp_http_timeout_omission_replacement_and_clear(tmp_path: Path, changes: dict) -> None:
    async with open_test_web(tmp_path) as web:
        path = web.host.config.mcp_config_path
        original = configure_mcp_server_config(
            path, "research", url="https://host.test/mcp", headers={"Authorization": "secret"}, timeout=12.5
        )
        neighbor = configure_mcp_server_config(path, "neighbor", url="https://neighbor.test/mcp", timeout=20)
        response = await web.client.patch("/api/integrations/mcp/research", json=changes)
        assert response.status_code == 200
        assert response.json()["requires_restart"] is True
        expected = {**original, **changes}
        if changes.get("timeout", 12.5) is None:
            expected.pop("timeout")
        assert read_mcp_server_configs(path) == [expected, neighbor]
        public = response.json()["server"]
        assert public.get("timeout") == expected.get("timeout")
        assert "secret" not in response.text
        listed = (await web.client.get("/api/integrations/mcp")).json()["servers"][0]
        assert listed == public
