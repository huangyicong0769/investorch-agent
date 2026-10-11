"""The HTTP boundary for a loopback-only browser application."""

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}


def install_http_security(app: FastAPI) -> None:
    @app.middleware("http")
    async def protect_local_requests(request: Request, call_next):
        def reject(message: str, status: int = 403) -> JSONResponse:
            return JSONResponse(
                status_code=status, content={"error": {"code": "request_rejected", "message": message, "details": {}}}
            )

        try:
            target = urlsplit(str(request.url))
            if target.hostname not in LOOPBACK_NAMES or target.username or target.password:
                return reject("A loopback Host is required.")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if origin is not None:
                    source = urlsplit(origin)
                    if (
                        source.scheme != target.scheme
                        or source.hostname != target.hostname
                        or (source.port or 80) != (target.port or 80)
                        or source.username
                        or source.password
                        or source.path not in ("", "/")
                        or source.query
                        or source.fragment
                    ):
                        return reject("State changes require the same browser origin.")
                elif request.headers.get("sec-fetch-site") is not None or request.headers.get("referer"):
                    return reject("Browser state changes require an Origin header.")
                # Non-browser local clients may omit Origin and Fetch Metadata.
                if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
                    return reject("Cross-site state changes are not allowed.")
                content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
                has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
                if (has_body or content_type) and content_type != "application/json":
                    return reject("State changes accept application/json only.", 415)
        except ValueError:
            return reject("Invalid Host or Origin.")
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "img-src 'self' data: https:"
        if request.url.path.startswith(("/api/config", "/api/setup", "/api/integrations", "/api/system")):
            response.headers["Cache-Control"] = "no-store"
        return response
