import httpx
import pytest

from investorch.qmt.client import QMTClient
from investorch.qmt.config import QMTConnectionProfile
from investorch.qmt.errors import QMTProtocolError, QMTRejectedError, QMTTransportError


async def test_client_reuses_connection_and_does_not_retry_uncertain_writes():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer private"
        raise httpx.ReadTimeout("lost response", request=request)

    profile = QMTConnectionProfile(
        "node", "https://node/mcp", "https://node/api/v1", {"Authorization": "Bearer private"}, 7
    )
    async with QMTClient(profile, transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(QMTTransportError):
            await client.stage_deployment("session", "deployment", {})
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/deployments/deployment"
    assert requests[0].headers["X-InvestOrch-Control-Session"] == "session"
    assert requests[0].extensions["timeout"]["read"] == 7


@pytest.mark.parametrize("body", [{"session_id": "x"}, {"session_id": "x", "lease_timeout_seconds": True}])
async def test_malformed_success_is_protocol_uncertainty(body):
    profile = QMTConnectionProfile("node", "http://node/mcp", "http://node/api/v1", {}, 7)
    async with QMTClient(profile, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        with pytest.raises(QMTProtocolError):
            await client.open_control_session()


async def test_definitive_rejection_preserves_code_and_retryability():
    profile = QMTConnectionProfile("node", "http://node/mcp", "http://node/api/v1", {}, 7)
    response = httpx.Response(409, json={"code": "STALE_CONTROL_SESSION", "message": "stale", "retryable": True})
    async with QMTClient(profile, transport=httpx.MockTransport(lambda r: response)) as client:
        with pytest.raises(QMTRejectedError) as caught:
            await client.get_next_fact("old")
    assert caught.value.code == "STALE_CONTROL_SESSION"
    assert caught.value.retryable is True
