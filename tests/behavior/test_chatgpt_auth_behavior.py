from __future__ import annotations

import time
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from investorch.llm.oauth import ChatGPTAuth, ChatGPTAuthError


class CredentialStore:
    def __init__(self):
        self.records = {}
        self.fail_save = False

    def oauth_credential(self, name, saved=False):
        return deepcopy(self.records.get(name))

    def save_oauth_credential(self, name, value):
        if self.fail_save:
            raise OSError("disk unavailable")
        self.records[name] = deepcopy(value)

    def ensure_chatgpt_host_id(self):
        return "urn:uuid:8b0488c0-369b-4b70-ab70-d530e5bb0f40"


@pytest.fixture
def oauth_service():
    store = CredentialStore()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid="test", alg="RS256", use="sig")
    state = {"nonce": None, "subject": "account-1", "requests": [], "refreshes": 0}

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
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path.endswith("/revoke"):
            return httpx.Response(200)
        form = parse_qs(request.content.decode())
        refresh = form["grant_type"] == ["refresh_token"]
        if refresh:
            state["refreshes"] += 1
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
                "scope": "openid offline_access resource.invoke chatgpt.tokens.use.direct",
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
    assert store.records["personal"]["client_id"] == "oaiapp_test"
    assert store.records["personal"]["subject"] == "account-1"
    assert store.records["personal"]["access_token"] is None
    with pytest.raises(ChatGPTAuthError, match="Sign in"):
        await auth.access_token("personal")
    await auth.close()
    await client.aclose()
