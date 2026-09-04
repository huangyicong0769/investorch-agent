from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from investorch_qmt.auth import BearerAuthMiddleware

TOKEN = "correct-token-with-at-least-32-characters"
ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]
]


async def terminal_app(scope, receive, send) -> None:
    status = 204 if scope["path"].startswith(("/mcp", "/healthz")) else 404
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def request(app: ASGIApp, path: str, headers: list[tuple[bytes, bytes]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "server": ("test", 80),
            "client": ("client", 1234),
        },
        receive,
        send,
    )
    return messages


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/session", "/healthz", "/healthz/"])
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Basic credentials")],
        [(b"authorization", b"Bearer")],
        [(b"authorization", b"Bearer wrong-token")],
        [(b"authorization", f"Bearer  {TOKEN}".encode())],
        [(b"authorization", f"Bearer {TOKEN} extra".encode())],
        [(b"authorization", f"Bearer {TOKEN}".encode()), (b"authorization", f"Bearer {TOKEN}".encode())],
    ],
)
async def test_protected_routes_reject_missing_malformed_or_duplicate_authorization(
    path: str,
    headers: list[tuple[bytes, bytes]],
) -> None:
    messages = await request(BearerAuthMiddleware(terminal_app, TOKEN), path, headers)

    start, body = messages
    response_headers = dict(start["headers"])
    assert start["status"] == 401
    assert response_headers[b"www-authenticate"] == b"Bearer"
    assert response_headers[b"content-type"] == b"application/json"
    assert json.loads(body["body"]) == {"error": "unauthorized"}
    assert TOKEN.encode() not in body["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
async def test_valid_token_reaches_protected_application(scheme: str) -> None:
    messages = await request(
        BearerAuthMiddleware(terminal_app, TOKEN),
        "/mcp",
        [(b"authorization", f"{scheme} {TOKEN}".encode())],
    )

    assert messages[0]["status"] == 204


@pytest.mark.asyncio
async def test_unknown_path_preserves_application_404_without_authorization() -> None:
    messages = await request(BearerAuthMiddleware(terminal_app, TOKEN), "/whatever", [])

    assert messages[0]["status"] == 404


@pytest.mark.asyncio
async def test_auth_failure_log_never_contains_token(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="investorch_qmt.auth"):
        messages = await request(
            BearerAuthMiddleware(terminal_app, TOKEN),
            "/healthz",
            [(b"authorization", f"Bearer {TOKEN}x".encode())],
        )

    assert messages[0]["status"] == 401
    assert TOKEN not in caplog.text
