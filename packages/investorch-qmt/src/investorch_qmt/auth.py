from __future__ import annotations

import hmac
import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

_LOGGER = logging.getLogger(__name__)
_UNAUTHORIZED_BODY = json.dumps({"error": "unauthorized"}, separators=(",", ":")).encode()


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_protected(scope["path"]):
            await self._app(scope, receive, send)
            return

        authorization = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
        if len(authorization) != 1 or not self._is_authorized(authorization[0]):
            _LOGGER.warning("Bearer authentication failed for path %s", scope["path"])
            await _send_unauthorized(send)
            return

        await self._app(scope, receive, send)

    def _is_authorized(self, header: bytes) -> bool:
        scheme, separator, credentials = header.partition(b" ")
        if separator != b" " or scheme.lower() != b"bearer" or not credentials:
            return False
        if any(character in b" \t\r\n" for character in credentials):
            return False
        return hmac.compare_digest(credentials, self._token)


def _is_protected(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/") or path in {"/healthz", "/healthz/"}


async def _send_unauthorized(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate", b"Bearer"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
