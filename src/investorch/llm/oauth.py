from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/settings/usage"
TERMINAL_REFRESH_ERRORS = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)


class CredentialConfig(Protocol):
    def oauth_credential(self, name: str, *, saved: bool = False) -> dict[str, Any] | None: ...
    def save_oauth_credential(self, name: str, value: dict[str, Any]) -> None: ...
    def ensure_chatgpt_host_id(self) -> str: ...


class ChatGPTAuthError(Exception):
    """An intentionally credential-free authentication failure."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Attempt:
    credential: str
    redirect_uri: str
    verifier: str
    nonce: str
    client_id: str | None
    subject: str | None
    expires: float


def _timestamp(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = (
            datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if isinstance(value, str) else float(value)
        )
        if not math.isfinite(result) or isinstance(value, bool):
            raise ValueError
        return result
    except (TypeError, ValueError, OverflowError):
        raise ChatGPTAuthError("invalid_token_response", "ChatGPT returned an invalid refresh time.") from None


def _cleared(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "access_token": None,
        "refresh_token": None,
        "id_token": None,
        "expires_at": 0.0,
        "earliest_refresh_at": None,
        "scopes": [],
        "pending_rotation": None,
    }


class ChatGPTAuth:
    """Own OAuth transactions and rotating credentials for one application host."""

    def __init__(self, config: CredentialConfig, *, http_client: httpx.AsyncClient | None = None):
        self.config = config
        self._http = http_client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self._owns_http = http_client is None
        self._attempts: dict[str, _Attempt] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._closed = False

    def _lock(self, credential: str) -> asyncio.Lock:
        return self._locks.setdefault(credential, asyncio.Lock())

    def _ensure_open(self) -> None:
        if self._closed:
            raise ChatGPTAuthError("auth_closed", "ChatGPT authentication is shutting down.", status=503)

    def begin(self, credential: str, redirect_uri: str, *, reconsent: bool = False) -> dict[str, str]:
        self._ensure_open()
        uri = urlsplit(redirect_uri)
        if (
            uri.scheme != "http"
            or uri.hostname != "127.0.0.1"
            or uri.path != "/auth/callback"
            or uri.query
            or uri.fragment
            or uri.username
        ):
            raise ChatGPTAuthError("invalid_callback", "Use the local 127.0.0.1 /auth/callback address.")
        if not credential or len(credential) > 128 or any(not (c.isalnum() or c in "_-") for c in credential):
            raise ChatGPTAuthError("invalid_credential", "Choose a valid ChatGPT credential name.")
        record = self.config.oauth_credential(credential) or {}
        now = time.time()
        self._attempts = {s: a for s, a in self._attempts.items() if a.expires > now and a.credential != credential}
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        self._attempts[state] = _Attempt(
            credential, redirect_uri, verifier, nonce, record.get("client_id"), record.get("subject"), now + 600
        )
        query = {
            "client_id": record.get("client_id") or "dynamic_agent_client",
            "ext_agent_host_id": self.config.ensure_chatgpt_host_id(),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
        }
        if not record.get("client_id"):
            query["agent_name_hint"] = "InvestOrch Agent"
        if reconsent:
            query["prompt"] = "consent"
        # Retained ID tokens stay in protected storage, never browser-launch arguments.
        return {"authorization_url": f"{AUTHORIZE_URL}?{urlencode(query)}", "state": state}

    def cancel(self, state: str) -> None:
        self._attempts.pop(state, None)

    def status(self, credential: str) -> dict[str, Any]:
        record = self.config.oauth_credential(credential) or {}
        connected = bool(record.get("access_token"))
        return {
            "credential": credential,
            "status": "connected" if connected else "disconnected",
            "plan_use": connected and PLAN_SCOPE in record.get("scopes", []),
            "display_name": record.get("display_name"),
            "manage_usage_url": USAGE_URL,
            "refresh_pending": bool(record.get("pending_rotation") or credential in self._pending),
        }

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise ChatGPTAuthError("network_error", "ChatGPT could not be reached. Try again.", status=503) from None

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ChatGPTAuthError("invalid_response", "ChatGPT returned an unreadable response.", status=502)
        return data

    async def _discovery(self) -> dict[str, Any]:
        response = await self._request("GET", f"{ISSUER}/.well-known/openid-configuration")
        data = self._json(response)
        if response.status_code != 200 or data.get("issuer") != ISSUER:
            raise ChatGPTAuthError("identity_unavailable", "ChatGPT identity verification is unavailable.", status=503)
        for field in ("jwks_uri", "revocation_endpoint"):
            value = data.get(field)
            if (
                not isinstance(value, str)
                or urlsplit(value).scheme != "https"
                or urlsplit(value).netloc != "auth.openai.com"
            ):
                raise ChatGPTAuthError("invalid_discovery", "ChatGPT identity endpoints could not be verified.")
        return data

    async def _identity(self, token: str, client_id: str, nonce: str | None = None) -> dict[str, Any]:
        discovery = await self._discovery()
        response = await self._request("GET", discovery["jwks_uri"])
        if response.status_code != 200:
            raise ChatGPTAuthError("identity_unavailable", "ChatGPT identity verification is unavailable.", status=503)
        try:
            header = jwt.get_unverified_header(token)
            keys = jwt.PyJWKSet.from_dict(self._json(response))
            key = next(k for k in keys.keys if k.key_id == header.get("kid") and k.algorithm_name == "RS256")
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=ISSUER,
                audience=client_id,
                options={"require": ["iss", "aud", "exp", "iat", "sub"]},
                leeway=5,
            )
            if (
                not isinstance(claims["sub"], str)
                or not claims["sub"]
                or (nonce is not None and claims.get("nonce") != nonce)
            ):
                raise ValueError
            if claims.get("azp", client_id) != client_id or (
                isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != client_id
            ):
                raise ValueError
            return claims
        except (jwt.PyJWTError, ValueError, KeyError, StopIteration, TypeError):
            raise ChatGPTAuthError(
                "invalid_id_token", "ChatGPT identity could not be verified. Sign in again."
            ) from None

    async def _token(self, fields: dict[str, str]) -> dict[str, Any]:
        response = await self._request("POST", TOKEN_URL, data=fields)
        data = self._json(response)
        if response.status_code != 200:
            code = data.get("error")
            known = TERMINAL_REFRESH_ERRORS | {"invalid_client", "access_denied"}
            safe_code = code if isinstance(code, str) and code in known else "token_request_failed"
            raise ChatGPTAuthError(
                safe_code,
                "ChatGPT authorization failed. Sign in again or retry later.",
                status=503 if response.status_code >= 500 else 400,
            )
        return data

    @staticmethod
    def _tokens(data: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        scope = data.get("scope", " ".join(previous.get("scopes", [])) if previous else None)
        if not isinstance(scope, str):
            raise ChatGPTAuthError("invalid_token_response", "ChatGPT did not confirm granted permissions.")
        scopes = scope.split()
        expires = data.get("expires_in")
        if (
            not isinstance(data.get("access_token"), str)
            or not data["access_token"]
            or not isinstance(expires, int | float)
            or isinstance(expires, bool)
            or not math.isfinite(expires)
            or expires <= 0
            or str(data.get("token_type", "")).lower() != "bearer"
            or ((previous is not None or "offline_access" in scopes) and not data.get("refresh_token"))
        ):
            raise ChatGPTAuthError("invalid_token_response", "ChatGPT returned incomplete credentials. Sign in again.")
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "id_token": data.get("id_token", previous.get("id_token") if previous else None),
            "expires_at": time.time() + expires,
            "earliest_refresh_at": _timestamp(data.get("earliest_refresh_at")),
            "scopes": scopes,
        }

    def _save(self, credential: str, record: dict[str, Any]) -> None:
        try:
            self.config.save_oauth_credential(credential, record)
        except Exception:
            raise ChatGPTAuthError(
                "credential_save_failed",
                "ChatGPT credentials could not be saved. Check local storage before retrying.",
                status=503,
            ) from None

    async def complete(
        self, *, state: str, code: str | None = None, client_id: str | None = None, error: str | None = None
    ) -> dict[str, Any]:
        self._ensure_open()
        attempt = self._attempts.pop(state, None)
        if attempt is None or attempt.expires <= time.time():
            raise ChatGPTAuthError("invalid_state", "This sign-in attempt expired or was already used. Start again.")
        if error:
            raise ChatGPTAuthError("access_denied", "ChatGPT sign-in was cancelled or declined.")
        issued = client_id or attempt.client_id
        if (
            not issued
            or issued == "dynamic_agent_client"
            or not code
            or (attempt.client_id and issued != attempt.client_id)
        ):
            raise ChatGPTAuthError(
                "registration_incomplete", "ChatGPT returned a different or incomplete registration."
            )
        async with self._lock(attempt.credential):
            old = self.config.oauth_credential(attempt.credential) or {}
            if old.get("subject") != attempt.subject or old.get("client_id") != attempt.client_id:
                raise ChatGPTAuthError("connection_changed", "This ChatGPT connection changed. Start sign-in again.")
            # Retain the issued registration if a one-use code expires before exchange.
            if not old:
                old = {"client_id": issued, "subject": None, "scopes": []}
                self._save(attempt.credential, old)
            data = await self._token(
                {
                    "grant_type": "authorization_code",
                    "client_id": issued,
                    "code": code,
                    "code_verifier": attempt.verifier,
                    "redirect_uri": attempt.redirect_uri,
                    "resource": RESOURCE,
                }
            )
            claims = await self._identity(data.get("id_token", ""), issued, attempt.nonce)
            if attempt.subject and claims["sub"] != attempt.subject:
                raise ChatGPTAuthError(
                    "account_mismatch", "Choose the original ChatGPT account or add another connection."
                )
            tokens = self._tokens(data)
            self._save(
                attempt.credential,
                {
                    "client_id": issued,
                    "subject": claims["sub"],
                    "display_name": claims.get("name") or old.get("display_name"),
                    **tokens,
                    "pending_rotation": None,
                },
            )
            self._pending.pop(attempt.credential, None)
        return self.status(attempt.credential)

    async def _refresh(self, credential: str, record: dict[str, Any]) -> dict[str, Any]:
        pending = self._pending.get(credential) or record.get("pending_rotation")
        if not pending:
            try:
                data = await self._token(
                    {
                        "grant_type": "refresh_token",
                        "client_id": record["client_id"],
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    }
                )
            except ChatGPTAuthError as exc:
                if exc.code in TERMINAL_REFRESH_ERRORS:
                    self._save(credential, _cleared(record))
                raise
            pending = {**self._tokens(data, record), "received_at": time.time()}
            self._pending[credential] = pending
        # The successor refresh token must survive a subsequent JWKS outage.
        self._save(credential, {**record, "pending_rotation": pending})
        if pending.get("id_token") and pending["id_token"] != record.get("id_token"):
            claims = await self._identity(pending["id_token"], record["client_id"])
            if claims["sub"] != record["subject"]:
                self._save(credential, _cleared(record))
                self._pending.pop(credential, None)
                raise ChatGPTAuthError("account_mismatch", "The refreshed identity changed. Sign in again.")
        replacement = {**record, **{k: v for k, v in pending.items() if k != "received_at"}, "pending_rotation": None}
        self._save(credential, replacement)
        self._pending.pop(credential, None)
        return replacement

    async def access_token(self, credential: str) -> str:
        self._ensure_open()
        async with self._lock(credential):
            record = self.config.oauth_credential(credential) or {}
            if not record.get("access_token"):
                raise ChatGPTAuthError("sign_in_required", "Sign in with ChatGPT to use this connection.", status=401)
            if PLAN_SCOPE not in record.get("scopes", []):
                raise ChatGPTAuthError(
                    "plan_use_disabled", "Enable ChatGPT plan usage for this connection.", status=403
                )
            now = time.time()
            pending = credential in self._pending or record.get("pending_rotation")
            if pending or record.get("expires_at", 0) <= now + 120:
                earliest = record.get("earliest_refresh_at") or 0
                if pending or earliest <= now:
                    if not pending and not record.get("refresh_token"):
                        raise ChatGPTAuthError("sign_in_required", "Sign in again to renew ChatGPT access.", status=401)
                    # Finish the rotating-token write even if the originating Run is cancelled.
                    task = asyncio.create_task(self._refresh(credential, record))
                    try:
                        record = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        try:
                            await task
                        finally:
                            raise
            if record.get("expires_at", 0) <= time.time():
                raise ChatGPTAuthError(
                    "refresh_not_ready",
                    "ChatGPT access expired before renewal is permitted. Try again shortly.",
                    status=503,
                )
            if PLAN_SCOPE not in record.get("scopes", []):
                raise ChatGPTAuthError(
                    "plan_use_disabled", "Enable ChatGPT plan usage for this connection.", status=403
                )
            return record["access_token"]

    async def models(self, credential: str) -> list[dict[str, str]]:
        token = await self.access_token(credential)
        response = await self._request("GET", f"{RESOURCE}/models", headers={"Authorization": f"Bearer {token}"})
        data = self._json(response)
        if response.status_code != 200 or not isinstance(data.get("models"), list):
            raise ChatGPTAuthError(
                "catalog_unavailable", "The ChatGPT model catalog is unavailable. Try again.", status=503
            )
        return [
            {"slug": m["slug"], "display_name": m.get("display_name", m["slug"])}
            for m in data["models"]
            if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str)
        ]

    async def logout(self, credential: str) -> dict[str, Any]:
        self._ensure_open()
        self._attempts = {s: a for s, a in self._attempts.items() if a.credential != credential}
        async with self._lock(credential):
            record = self.config.oauth_credential(credential) or {}
            pending = self._pending.get(credential) or record.get("pending_rotation") or {}
            token = pending.get("refresh_token") or record.get("refresh_token")
            revoked = not token
            if token:
                try:
                    discovery = await self._discovery()
                    response = await self._request(
                        "POST",
                        discovery["revocation_endpoint"],
                        data={"token": token, "token_type_hint": "refresh_token", "client_id": record["client_id"]},
                    )
                    revoked = response.status_code == 200
                except ChatGPTAuthError:
                    revoked = False
            if record:
                self._save(credential, _cleared(record))
            self._pending.pop(credential, None)
            return {
                **self.status(credential),
                "revoked": revoked,
                "message": "Signed out."
                if revoked
                else "Signed out locally; remote revocation was not confirmed. Disconnect the app in ChatGPT Settings.",
            }

    async def close(self) -> None:
        self._closed = True
        self._attempts.clear()
        # Wait for in-progress rotation persistence before closing its HTTP transport.
        for lock in tuple(self._locks.values()):
            async with lock:
                pass
        if self._owns_http:
            await self._http.aclose()
