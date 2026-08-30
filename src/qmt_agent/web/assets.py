from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

STATIC_DIR = Path(__file__).with_name("static")
MISSING_ASSETS_MESSAGE = "WebUI assets are missing. Build the frontend first."


class ImmutableStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def install_webui_routes(app: FastAPI) -> None:
    index_path = STATIC_DIR / "index.html"
    assets_dir = STATIC_DIR / "assets"
    if not index_path.is_file() or not assets_dir.is_dir():
        raise RuntimeError(MISSING_ASSETS_MESSAGE)

    app.mount("/assets", ImmutableStaticFiles(directory=assets_dir), name="webui-assets")

    @app.get("/", include_in_schema=False)
    async def webui_index() -> FileResponse:
        return _index_response(index_path)

    @app.get("/{path:path}", include_in_schema=False)
    async def webui_fallback(path: str) -> FileResponse:
        reserved = ("api", "ws", "assets")
        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in reserved):
            raise HTTPException(status_code=404, detail="Not Found")
        return _index_response(index_path)


def _index_response(index_path: Path) -> FileResponse:
    return FileResponse(index_path, headers={"Cache-Control": "no-cache"})
