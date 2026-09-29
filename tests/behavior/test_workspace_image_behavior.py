from __future__ import annotations

import base64
from pathlib import Path

import pytest

from tests.support.web import open_test_web

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=")


@pytest.mark.asyncio
async def test_workspace_image_serves_current_file_without_exposing_other_files(tmp_path: Path) -> None:
    async with open_test_web(tmp_path, {"images": {"max_image_bytes": 128}}) as web:
        workspace = web.host.config.workspace_dir
        chart = workspace / "图表 1.png"
        chart.write_bytes(PNG)

        async def read(path: str):
            return await web.client.get("/api/workspace/image", params={"path": path})

        response = await read(chart.name)
        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store"

        chart.write_text("private text disguised as a PNG")
        assert (await read(chart.name)).status_code == 415
        chart.write_bytes(PNG + b"x" * 128)
        assert (await read(chart.name)).status_code == 413

        outside = workspace.parent / "outside.png"
        outside.write_bytes(PNG)
        (workspace / "escape.png").symlink_to(outside)
        for path in (str(outside), "../outside.png", "escape.png", "missing.png", "."):
            response = await read(path)
            assert response.status_code == 404
            assert PNG not in response.content
