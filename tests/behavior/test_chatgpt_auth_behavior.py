from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from investorch.config import load_config
from investorch.llm.oauth import ChatGPTAuth
from tests.support.config import make_test_config


@pytest.fixture
def oauth_service(tmp_path):
    store = make_test_config(
        tmp_path, {"models": {"chatgpt": {"type": "chatgpt", "credentials": "personal"}}}, initialize_state=False
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid="test", alg="RS256", use="sig")
    state = {
        "nonce": None,
        "subject": "account-1",
        "requests": [],
        "refreshes": 0,
        "token_error": None,
        "jwks_error": False,
    }

    async def handle(request):
        state["requests"].append(request)
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://auth.openai.com",
                    "jwks_uri": "https://auth.openai.com/.well-known/jwks.json",
                    "revocation_endpoint": "https://auth.openai.com/api/accounts/oauth/revoke",
                },
            )
        if request.url.path.endswith("jwks.json"):
            if state["jwks_error"]:
                return httpx.Response(503)
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path.endswith("/revoke"):
            return httpx.Response(state.get("revoke_status", 200))
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"slug": "account-model", "display_name": "Account model", "visibility": "list"},
                        {"slug": "hidden-model", "display_name": "Hidden model", "visibility": "hidden"},
                    ]
                },
            )
        form = parse_qs(request.content.decode())
        if state.get("pause_token"):
            started, release = state["pause_token"]
            started.set()
            await release.wait()
        refresh = form["grant_type"] == ["refresh_token"]
        if refresh:
            state["refreshes"] += 1
        if state["token_error"]:
            return httpx.Response(
                400, json={"error": state["token_error"], "error_description": "must-not-be-exposed-token"}
            )
        claims = {
            "iss": "https://auth.openai.com",
            "aud": "oaiapp_test",
            "sub": state["subject"],
            "iat": int(time.time()),
            "exp": int(time.time()) + state.get("id_lifetime", 3600),
        }
        if not refresh:
            claims["nonce"] = state["nonce"]
        claims.update(state.get("claims", {}))
        data = {
            "access_token": "new-access" if refresh else "access",
            "refresh_token": "new-refresh" if refresh else "refresh",
            "id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"}),
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": state.get("scope", "openid offline_access resource.invoke chatgpt.tokens.use.direct"),
        }
        if state.get("identity_only"):
            data = {"id_token": data["id_token"], "scope": "openid profile email"}
        if refresh and state.get("omit_refresh_identity"):
            data.pop("id_token")
            data.pop("scope")
        return httpx.Response(200, json=data)

    return store, state, httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def test_chatgpt_only_setup_authorizes_and_starts_the_real_host_without_api_keys(
    tmp_path, oauth_service, monkeypatch
):
    from investorch.application import ApplicationHost
    from investorch.config import REQUIRED_AGENT_ROLES
    from investorch.web.server import create_web_app

    _, oauth_state, oauth_http = oauth_service
    root = tmp_path / "chatgpt-only"
    config = load_config(root=root)
    assert not root.exists()
    assert config.public()["models"] == {}
    assert config.public()["secrets"] == {}
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "investorch.web.server.ChatGPTAuth", lambda configured: ChatGPTAuth(configured, http_client=oauth_http)
    )
    monkeypatch.setattr("investorch.web.server.configure_logging", lambda _: None)
    app = create_web_app(config)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client,
        ):
            assert (await client.get("/api/setup/state")).json()["status"] == "setup_required"
            assert app.state.host is None
            setup_auth = app.state.chatgpt_auth
            assert setup_auth.config is config
            changes = {"models.chatgpt": {"type": "chatgpt", "credentials": "personal"}}
            for role in REQUIRED_AGENT_ROLES:
                changes[f"agents.{role}"] = {
                    "model": "chatgpt",
                    "name": "account-model",
                    "reasoning_effort": "high",
                    "context_window_tokens": 200000,
                }
            saved = await client.patch("/api/setup/config", json={"changes": changes})
            assert saved.status_code == 200, saved.text
            incomplete = await client.post("/api/setup/complete", json={})
            assert incomplete.status_code == 422
            assert app.state.host is None
            login = await client.post("/api/auth/chatgpt/chatgpt/login", json={})
            assert login.status_code == 200, login.text
            query = parse_qs(urlsplit(login.json()["authorization_url"]).query)
            oauth_state["nonce"] = query["nonce"][0]
            callback = await client.get(
                "/auth/callback",
                params={"state": login.json()["state"], "code": "one-use-code", "client_id": "oaiapp_test"},
            )
            assert callback.status_code == 200, callback.text
            assert (await client.get("/api/auth/chatgpt/chatgpt")).json()["plan_use"] is True
            assert (await client.get("/api/setup/state")).json()["missing"] == []
            completed = await client.post("/api/setup/complete", json={})
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "ready"
            host = app.state.host
            assert isinstance(host, ApplicationHost)
            assert host.config is app.state.config is config
            assert host.state.chatgpt_auth is app.state.chatgpt_auth is setup_auth
            assert setup_auth.config is host.config
            assert all(host.config.agent_model(role).model == "chatgpt" for role in REQUIRED_AGENT_ROLES)
            persisted = load_config(root=root)
            assert set(persisted.public()["models"]) == {"chatgpt"}
            assert set(persisted.public()["secrets"]) == {"personal"}
            assert persisted.secrets == {}
            session = await client.post("/api/sessions", json={})
            assert session.status_code == 200, session.text
            session_id = session.json()["session"]["session_id"]
            assert host.session_models.view(session_id).effective.model == "chatgpt"
            assert (await client.get(f"/api/sessions/{session_id}/history")).status_code == 200
            assert (await client.get("/api/bootstrap")).status_code == 200
    finally:
        await oauth_http.aclose()
