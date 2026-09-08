from decimal import Decimal

import httpx
import pytest

from investorch.qmt.client import QMTClient
from investorch.qmt.config import QMTConnectionProfile
from investorch.qmt.contracts import TradeFact
from investorch.qmt.errors import QMTProtocolError, QMTRejectedError, QMTTransportError


def trade_payload():
    return {
        "schema_version": 1,
        "deployment_id": "deployment-1",
        "broker_trade_id": "trade-1",
        "instrument": {"code": "600519", "market": "XSHG"},
        "side": "BUY",
        "quantity": "100",
        "price": "10.50",
        "commission": "1.00",
        "tax": "0",
        "other_fee": "0",
        "effective_at": "2026-09-08T01:23:45+08:00",
    }


def profile():
    return QMTConnectionProfile(
        "desk", "http://node/mcp", "http://node/api/v1", {"Authorization": "Bearer test-secret"}, 2
    )


def test_trade_wire_preserves_decimal_economics_and_timestamp():
    parsed = TradeFact.parse(trade_payload())
    assert parsed.quantity == Decimal("100")
    assert parsed.price == Decimal("10.50")
    assert parsed.commission == Decimal("1")
    assert parsed.effective_at.isoformat() == "2026-09-08T01:23:45+08:00"
    assert parsed.instrument.code == "600519"


@pytest.mark.parametrize("field", ["quantity", "price", "commission", "tax", "other_fee"])
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "-1", " 1", 1, True, None])
def test_trade_rejects_nonfinite_negative_and_nonstring_amounts(field, value):
    payload = trade_payload()
    payload[field] = value
    with pytest.raises(QMTProtocolError):
        TradeFact.parse(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("quantity", "0"),
        ("price", "0"),
        ("deployment_id", ""),
        ("deployment_id", " deployment-1"),
        ("broker_trade_id", "trade-1 "),
        ("broker_trade_id", False),
        ("effective_at", "2026-09-08T01:23:45"),
        ("effective_at", "not-a-date"),
        ("instrument", {"code": "600519", "market": "XSHG", "extra": 1}),
        ("side", "sell"),
    ],
)
def test_trade_rejects_invalid_schema_identity_time_and_fields(field, value):
    payload = trade_payload()
    payload[field] = value
    with pytest.raises(QMTProtocolError):
        TradeFact.parse(payload)


def test_trade_requires_exact_top_level_field_set():
    missing = trade_payload()
    missing.pop("tax")
    extra = {**trade_payload(), "currency": "CNY"}
    for payload in (missing, extra):
        with pytest.raises(QMTProtocolError):
            TradeFact.parse(payload)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError])
async def test_transport_failure_is_unknown_without_hidden_retry(error_type):
    requests = []

    def respond(request):
        requests.append(request)
        raise error_type("interrupted", request=request)

    async with QMTClient(profile(), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(QMTTransportError):
            await client.stage_deployment("session-1", "deployment-1", {})
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/deployments/deployment-1"
    assert requests[0].headers["Authorization"] == "Bearer test-secret"
    assert requests[0].headers["X-InvestOrch-Control-Session"] == "session-1"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"code": "INVALID_REQUEST", "message": "Invalid input", "retryable": False}),
        (409, {"code": "STALE_CONTROL_SESSION", "message": "Expired", "retryable": True}),
        (500, {"code": "INTERNAL_ERROR", "message": "Unavailable", "retryable": True}),
    ],
)
async def test_valid_error_envelope_preserves_recovery_information(status, body):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    async with QMTClient(profile(), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(QMTRejectedError) as caught:
            await client.stage_deployment("session-1", "deployment-1", {})
    assert caught.value.code == body["code"]
    assert caught.value.retryable is body["retryable"]
    assert caught.value.status_code == status
    assert len(requests) == 1


@pytest.mark.parametrize("body", [[], {}, {"code": "BAD", "message": "bad", "retryable": 1}])
async def test_malformed_error_is_uncertain_not_definitive_rejection(body):
    async with QMTClient(profile(), transport=httpx.MockTransport(lambda _: httpx.Response(400, json=body))) as client:
        with pytest.raises(QMTProtocolError):
            await client.open_control_session()


async def test_non_json_stage_success_is_uncertain():
    async with QMTClient(
        profile(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"broken"))
    ) as client:
        with pytest.raises(QMTProtocolError):
            await client.stage_deployment("session-1", "deployment-1", {})


@pytest.mark.parametrize("lease", [True, 0, -1, "10", None])
async def test_session_rejects_invalid_lease(lease):
    async with QMTClient(
        profile(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"session_id": "s", "lease_timeout_seconds": lease})
        ),
    ) as client:
        with pytest.raises(QMTProtocolError):
            await client.open_control_session()


@pytest.mark.parametrize("field", ["deployment_id", "external_fact_id"])
async def test_next_fact_rejects_envelope_identity_mismatch(field):
    fact = {
        "fact_id": "f",
        "queue_sequence": 1,
        "deployment_id": "deployment-1",
        "fact_type": "TRADE_V1",
        "external_fact_id": "trade-1",
        "payload": trade_payload(),
    }
    fact[field] = "different"
    async with QMTClient(
        profile(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"fact": fact}))
    ) as client:
        with pytest.raises(QMTProtocolError):
            await client.get_next_fact("s")


@pytest.mark.parametrize("state", [[], {}])
async def test_malformed_status_value_is_a_protocol_error(state):
    body = {
        "service": {"status": "ready"},
        "qmt": {"status": "not_connected"},
        "control": {"status": state, "lease_expires_at": None},
        "deployments": [],
    }
    async with QMTClient(profile(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(QMTProtocolError):
            await client.get_node_status()


@pytest.mark.parametrize(
    ("field", "value"),
    [("deployment_id", "other"), ("status", []), ("portfolio_sync", {}), ("acked_core_sequence", True)],
)
async def test_stage_success_must_identify_valid_deployment_state(field, value):
    body = {
        "deployment_id": "deployment-1",
        "portfolio_id": "p",
        "broker_account_id": "b",
        "status": "STAGED",
        "acked_core_sequence": 0,
        "pending_fact_count": 0,
        "portfolio_sync": "SYNCED",
    }
    body[field] = value
    async with QMTClient(profile(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(QMTProtocolError):
            await client.stage_deployment("s", "deployment-1", {})


async def test_renew_cannot_silently_replace_session_authority():
    body = {"session_id": "other", "lease_timeout_seconds": 10, "expires_at": "2026-09-08T01:23:45+08:00"}
    async with QMTClient(profile(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(QMTProtocolError):
            await client.renew_control_session("original")


@pytest.mark.parametrize("sequence", [True, 2, "1"])
async def test_ack_response_must_confirm_exact_committed_sequence(sequence):
    body = {"fact_id": "f", "status": "ACKED", "committed_ledger_sequence": sequence}
    async with QMTClient(profile(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(QMTProtocolError):
            await client.ack_fact("s", "f", 1)
