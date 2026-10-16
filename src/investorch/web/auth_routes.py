from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from investorch.llm.oauth import ChatGPTAuth, ChatGPTAuthError

from .errors import APIError

router = APIRouter()


def _manager(request: Request) -> ChatGPTAuth:
    manager = getattr(request.app.state, "chatgpt_auth", None)
    if manager is None or getattr(request.app.state, "recovery_error", None):
        raise APIError(
            409, "auth_unavailable", "ChatGPT authentication is unavailable until instance recovery completes."
        )
    return manager


def _credential(request: Request, connection_id: str) -> str:
    config = request.app.state.config
    # Settings saves are restart-required, while authorization is immediately available.
    connection = config.public(saved=True)["models"].get(connection_id)
    if not connection or connection.get("type") != "chatgpt" or not connection.get("credentials"):
        raise APIError(422, "invalid_chatgpt_connection", "Save a ChatGPT connection before signing in.")
    return connection["credentials"]


def _error(exc: ChatGPTAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": str(exc), "details": {}}},
        headers={"Cache-Control": "no-store"},
    )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reconsent: bool = False


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: str


@router.post("/api/auth/chatgpt/cancel")
async def cancel_login(body: CancelRequest, request: Request) -> JSONResponse:
    _manager(request).cancel(body.state)
    return JSONResponse({"cancelled": True}, headers={"Cache-Control": "no-store"})


@router.get("/api/auth/chatgpt/{connection_id}")
async def auth_status(connection_id: str, request: Request) -> JSONResponse:
    return JSONResponse(
        _manager(request).status(_credential(request, connection_id)), headers={"Cache-Control": "no-store"}
    )


@router.post("/api/auth/chatgpt/{connection_id}/login")
async def start_login(connection_id: str, body: LoginRequest, request: Request) -> JSONResponse:
    target = urlsplit(str(request.base_url))
    # Callback hostname is fixed by the provider; localhost UI still uses this listener.
    port = target.port or 80
    try:
        manager = _manager(request)
        manager.callback_base_url = f"http://127.0.0.1:{port}"
        result = manager.begin(
            _credential(request, connection_id), f"http://127.0.0.1:{port}/auth/callback", reconsent=body.reconsent
        )
    except ChatGPTAuthError as exc:
        return _error(exc)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/api/auth/chatgpt/{connection_id}/models")
async def account_models(connection_id: str, request: Request) -> JSONResponse:
    try:
        models = await _manager(request).models(_credential(request, connection_id))
    except ChatGPTAuthError as exc:
        return _error(exc)
    return JSONResponse({"models": models}, headers={"Cache-Control": "no-store"})


@router.post("/api/auth/chatgpt/{connection_id}/logout")
async def sign_out(connection_id: str, request: Request) -> JSONResponse:
    try:
        result = await _manager(request).logout(_credential(request, connection_id))
    except ChatGPTAuthError as exc:
        return _error(exc)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/auth/callback")
async def oauth_callback(request: Request) -> HTMLResponse:
    query = request.query_params
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if (
        request.url.hostname != "127.0.0.1"
        or len(query.getlist("state")) != 1
        or any(len(query.getlist(key)) > 1 for key in ("code", "client_id", "error"))
    ):
        return HTMLResponse(
            "Invalid ChatGPT callback. Return to the application and start again.", status_code=400, headers=headers
        )
    try:
        await _manager(request).complete(
            state=query["state"], code=query.get("code"), client_id=query.get("client_id"), error=query.get("error")
        )
    except ChatGPTAuthError:
        # Do not echo provider strings, state, authorization code or URL into HTML.
        return HTMLResponse(
            "ChatGPT sign-in could not finish. Return to the application and start again.",
            status_code=400,
            headers=headers,
        )
    return HTMLResponse(
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>ChatGPT connected</title>"
        "<h1>ChatGPT connected</h1><p>Return to InvestOrch. You can close this tab.</p></html>",
        headers=headers,
    )
