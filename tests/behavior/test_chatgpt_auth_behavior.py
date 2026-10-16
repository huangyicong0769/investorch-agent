from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
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


async def sign_in(auth, state):
    attempt = auth.begin("personal", "http://127.0.0.1:8765/auth/callback")
    query = parse_qs(urlsplit(attempt["authorization_url"]).query)
    state["nonce"] = query["nonce"][0]
    return await auth.complete(state=attempt["state"], code="one-use-code", client_id="oaiapp_test")


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


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://untrusted.example"},
        {"aud": "another-client"},
        {"nonce": "wrong-nonce"},
        {"exp": 1},
        {"azp": "another-client"},
        {"sub": ""},
    ],
)
async def test_invalid_signed_oidc_claims_never_replace_previous_credentials(oauth_service, claims):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    before = store.root_config_path.read_bytes()
    state["claims"] = claims
    with pytest.raises(ChatGPTAuthError) as error:
        await sign_in(auth, state)
    assert error.value.code == "invalid_id_token"
    assert store.root_config_path.read_bytes() == before
    assert auth.status("personal")["error"]["code"] == "invalid_id_token"


async def test_cancelled_sign_in_does_not_exchange_the_authorization_code(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    attempt = auth.begin("personal", "http://127.0.0.1:8765/auth/callback")
    auth.cancel(attempt["state"])
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.complete(state=attempt["state"], code="unused", client_id="oaiapp_test")
    assert error.value.code == "invalid_state"
    assert state["requests"] == []
    assert store.oauth_credential("personal") is None


async def test_cancelling_an_inflight_exchange_does_not_install_its_credentials(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    started, release = asyncio.Event(), asyncio.Event()
    state["pause_token"] = (started, release)
    attempt = auth.begin("personal", "http://127.0.0.1:8765/auth/callback")
    state["nonce"] = parse_qs(urlsplit(attempt["authorization_url"]).query)["nonce"][0]
    completing = asyncio.create_task(auth.complete(state=attempt["state"], code="code", client_id="oaiapp_test"))
    await started.wait()
    auth.cancel(attempt["state"])
    release.set()
    with pytest.raises(ChatGPTAuthError) as error:
        await completing
    assert error.value.code == "auth_cancelled"
    assert auth.status("personal")["status"] == "disconnected"
    assert load_config(root=store.root).oauth_credential("personal").get("access_token") is None


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


async def test_identity_only_response_without_access_token_remains_a_verified_login(oauth_service):
    store, state, client = oauth_service
    state["identity_only"] = True
    auth = ChatGPTAuth(store, http_client=client)
    result = await sign_in(auth, state)
    assert result["status"] == "connected"
    assert result["plan_use"] is False
    assert load_config(root=store.root).oauth_credential("personal")["subject"] == "account-1"
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.access_token("personal")
    assert error.value.code == "plan_use_disabled"


async def test_refresh_preserves_identity_and_scope_when_the_response_omits_them(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    old_identity = store.oauth_credential("personal")["id_token"]
    expire(store)
    state["omit_refresh_identity"] = True
    assert await auth.access_token("personal") == "new-access"
    assert store.oauth_credential("personal")["id_token"] == old_identity
    assert auth.status("personal")["plan_use"] is True


async def test_pending_identity_is_verified_at_receipt_after_a_prolonged_jwks_outage(oauth_service, monkeypatch):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    expire(store)
    state.update(jwks_error=True, id_lifetime=10)
    with pytest.raises(ChatGPTAuthError):
        await auth.access_token("personal")
    state["jwks_error"] = False

    class LaterDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=1)

    monkeypatch.setattr(jwt.api_jwt, "datetime", LaterDateTime)
    restarted = ChatGPTAuth(load_config(root=store.root), http_client=client)
    assert await restarted.access_token("personal") == "new-access"
    assert state["refreshes"] == 1


async def test_local_logout_reports_unconfirmed_remote_revocation(oauth_service):
    store, state, client = oauth_service
    auth = ChatGPTAuth(store, http_client=client)
    await sign_in(auth, state)
    state["revoke_status"] = 503
    result = await auth.logout("personal")
    assert result["status"] == "disconnected"
    assert result["revoked"] is False
    assert "not confirmed" in result["message"]
    assert len([r for r in state["requests"] if r.url.path.endswith("/revoke")]) == 2


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


async def test_terminal_browser_login_uses_one_local_listener_without_creating_a_runtime(
    oauth_service, monkeypatch, caplog
):
    store, state, provider = oauth_service
    caplog.set_level(logging.INFO, logger="uvicorn.access")

    def unexpected_host(*args, **kwargs):
        raise AssertionError("OAuth must not create an Application Host")

    monkeypatch.setattr("investorch.application.open_application_host", unexpected_host)
    auth = ChatGPTAuth(store, http_client=provider)
    before = set(store.root.rglob("*.db"))
    try:
        first, second = await asyncio.gather(auth.start_browser_login("chatgpt"), auth.start_browser_login("chatgpt"))
        first_query = parse_qs(urlsplit(first["authorization_url"]).query)
        query = parse_qs(urlsplit(second["authorization_url"]).query)
        assert first_query["redirect_uri"] == query["redirect_uri"]
        callback = query["redirect_uri"][0]
        assert urlsplit(callback).hostname == "127.0.0.1"
        state["nonce"] = query["nonce"][0]
        async with httpx.AsyncClient(trust_env=False, timeout=3) as browser:
            result = await browser.get(
                callback, params={"state": second["state"], "code": "secret-browser-code", "client_id": "oaiapp_test"}
            )
            assert result.status_code == 200
        assert auth.status("personal")["plan_use"] is True
        assert set(store.root.rglob("*.db")) == before
        assert "secret-browser-code" not in caplog.text
    finally:
        await auth.close()
    with pytest.raises(ChatGPTAuthError) as error:
        await auth.start_browser_login("chatgpt")
    assert error.value.code == "auth_closed"
