from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from investorch.config import load_config
from investorch.llm.oauth import ChatGPTAuth, ChatGPTAuthError
from investorch.web.auth_routes import router
from investorch.web.security import install_http_security
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

    def handle(request):
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
            return httpx.Response(200)
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
            "exp": int(time.time()) + 3600,
        }
        if not refresh:
            claims["nonce"] = state["nonce"]
        return httpx.Response(
            200,
            json={
                "access_token": "new-access" if refresh else "access",
                "refresh_token": "new-refresh" if refresh else "refresh",
                "id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"}),
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": state.get("scope", "openid offline_access resource.invoke chatgpt.tokens.use.direct"),
            },
        )

    return store, state, httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def sign_in(auth, state):
    attempt = auth.begin("personal", "http://127.0.0.1:8765/auth/callback")
    query = parse_qs(urlsplit(attempt["authorization_url"]).query)
    state["nonce"] = query["nonce"][0]
    return await auth.complete(state=attempt["state"], code="one-use-code", client_id="oaiapp_test")


async def test_user_can_register_and_sign_out_without_losing_account_registration(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    status = await sign_in(auth, state)
    assert status["status"] == "connected"
    assert status["plan_use"] is True
    assert "access_token" not in status
    assert await auth.access_token("personal") == "access"
    result = await auth.logout("personal")
    assert result["revoked"] is True
    saved = load_config(root=store.root).oauth_credential("personal")
    assert saved["client_id"] == "oaiapp_test"
    assert saved["subject"] == "account-1"
    assert saved.get("access_token") is None
    with pytest.raises(ChatGPTAuthError, match="Sign in"):
        await auth.access_token("personal")
    await auth.close()
    await client.aclose()


def expire(store):
    record = store.oauth_credential("personal")
    store.save_oauth_credential("personal", {**record, "expires_at": time.time() - 1})


async def test_two_concurrent_requests_rotate_once_and_save_the_successor(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    expire(store)
    assert await asyncio.gather(auth.access_token("personal"), auth.access_token("personal")) == [
        "new-access",
        "new-access",
    ]
    assert state["refreshes"] == 1
    saved = load_config(root=store.root).oauth_credential("personal")
    assert saved["refresh_token"] == "new-refresh"
    assert saved.get("pending_rotation") is None


async def test_invalid_state_and_replayed_callback_do_not_replace_credentials(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    before = store.root_config_path.read_bytes()
    attempt = auth.begin("personal", "http://127.0.0.1:8765/auth/callback")
    state["nonce"] = parse_qs(urlsplit(attempt["authorization_url"]).query)["nonce"][0]
    with pytest.raises(ChatGPTAuthError) as rejected:
        await auth.complete(state="unrelated", code="code", client_id="oaiapp_test")
    assert rejected.value.code == "invalid_state"
    assert store.root_config_path.read_bytes() == before
    await auth.complete(state=attempt["state"], code="code")
    with pytest.raises(ChatGPTAuthError) as replay:
        await auth.complete(state=attempt["state"], code="code")
    assert replay.value.code == "invalid_state"


async def test_reauthorization_rejects_another_identity_and_keeps_original_tokens(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    before = store.root_config_path.read_bytes()
    state["subject"] = "different-account"
    with pytest.raises(ChatGPTAuthError) as error:
        await sign_in(auth, state)
    assert error.value.code == "account_mismatch"
    assert store.root_config_path.read_bytes() == before


async def test_rotation_survives_a_jwks_outage_and_process_restart(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    expire(store)
    state["jwks_error"] = True
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.access_token("personal")
    assert error.value.code == "identity_unavailable"
    assert (
        load_config(root=store.root).oauth_credential("personal")["pending_rotation"]["refresh_token"] == "new-refresh"
    )
    state["jwks_error"] = False
    restarted = ChatGPTAuth(load_config(root=store.root), http_client=client)
    assert await restarted.access_token("personal") == "new-access"
    assert state["refreshes"] == 1


async def test_failed_rotation_write_is_retried_without_reusing_refresh_token(oauth_service, monkeypatch):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    expire(store)
    original = store.save_oauth_credential

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "save_oauth_credential", fail)
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.access_token("personal")
    assert error.value.code == "credential_save_failed"
    monkeypatch.setattr(store, "save_oauth_credential", original)
    assert await auth.access_token("personal") == "new-access"
    assert state["refreshes"] == 1
    assert load_config(root=store.root).oauth_credential("personal")["refresh_token"] == "new-refresh"


async def test_terminal_refresh_failure_requires_login_without_exposing_provider_secrets(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    expire(store)
    state["token_error"] = "invalid_grant"
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.access_token("personal")
    assert error.value.code == "invalid_grant"
    assert "must-not-be-exposed-token" not in str(error.value)
    assert auth.status("personal")["status"] == "disconnected"
    assert load_config(root=store.root).oauth_credential("personal")["client_id"] == "oaiapp_test"


async def test_identity_only_consent_is_saved_but_cannot_run_inference(oauth_service):
    store, state, client = oauth_service
    state["scope"] = "openid profile email"
    auth = ChatGPTAuth(store, http_client=client)
    result = await sign_in(auth, state)
    assert result["status"] == "connected"
    assert result["plan_use"] is False
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.access_token("personal")
    assert error.value.code == "plan_use_disabled"


async def test_refresh_waits_for_server_earliest_time_and_never_sends_expired_token(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    record = store.oauth_credential("personal")
    store.save_oauth_credential(
        "personal", {**record, "expires_at": time.time() + 30, "earliest_refresh_at": time.time() + 60}
    )
    assert await auth.access_token("personal") == "access"
    expire(store)
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.access_token("personal")
    assert error.value.code == "refresh_not_ready"
    assert state["refreshes"] == 0


async def test_setup_host_can_sign_in_list_models_and_logout_without_an_agent_host(oauth_service):
    store, state, provider = oauth_service
    app = FastAPI()
    app.state.config = store
    app.state.host = None
    app.state.recovery_error = None
    app.state.chatgpt_auth = ChatGPTAuth(store, http_client=provider)
    app.include_router(router)
    install_http_security(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        login = await client.post("/api/auth/chatgpt/chatgpt/login", json={})
        assert login.status_code == 200
        assert login.headers["cache-control"] == "no-store"
        query = parse_qs(urlsplit(login.json()["authorization_url"]).query)
        state["nonce"] = query["nonce"][0]
        assert query["redirect_uri"] == ["http://127.0.0.1:8765/auth/callback"]
        assert query["resource"] == ["https://api.openai.com/v1"]
        assert query["ext_agent_host_id"][0].startswith("urn:uuid:")
        callback = await client.get(
            "/auth/callback", params={"state": login.json()["state"], "code": "code", "client_id": "oaiapp_test"}
        )
        assert callback.status_code == 200
        assert "access" not in callback.text
        status = await client.get("/api/auth/chatgpt/chatgpt")
        assert status.json()["plan_use"] is True
        assert "refresh_token" not in status.text
        catalog = await client.get("/api/auth/chatgpt/chatgpt/models")
        assert catalog.json() == {"models": [{"slug": "account-model", "display_name": "Account model"}]}
        rejected = await client.post(
            "/api/auth/chatgpt/chatgpt/logout", json={}, headers={"Origin": "https://untrusted.example"}
        )
        assert rejected.status_code == 403
        assert (await client.get("/api/auth/chatgpt/chatgpt")).json()["plan_use"] is True
        logged_out = await client.post("/api/auth/chatgpt/chatgpt/logout", json={})
        assert logged_out.json()["status"] == "disconnected"
