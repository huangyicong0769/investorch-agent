"""One request per operation; recovery and retries belong to the coordinator."""

from typing import Any
from urllib.parse import quote

import httpx

from .config import QMTConnectionProfile
from .contracts import TradeFact, deployment_summary, object_value, sequence_value, text_value, timestamp_value
from .errors import QMTProtocolError, QMTRejectedError, QMTTransportError


class QMTClient:
    def __init__(self, profile: QMTConnectionProfile, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            base_url=profile.rest_base_url + "/",
            headers=dict(profile.headers),
            timeout=profile.timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, *, session_id: str | None = None, body: Any = None) -> dict:
        headers = {} if session_id is None else {"X-InvestOrch-Control-Session": session_id}
        try:
            response = await self._http.request(method, path, headers=headers, json=body)
        except httpx.TransportError as exc:
            raise QMTTransportError("Execution node request did not produce a response") from exc
        try:
            data = object_value(response.json())
        except ValueError as exc:
            raise QMTProtocolError("Execution node returned invalid JSON") from exc
        if not response.is_success:
            code, message, retryable = data.get("code"), data.get("message"), data.get("retryable")
            text_value(code)
            text_value(message)
            if type(retryable) is not bool:
                raise QMTProtocolError("Invalid error envelope")
            raise QMTRejectedError(code, message, retryable, response.status_code)
        return data

    async def get_node_status(self) -> dict:
        data = await self._request("GET", "node/status")
        for key in ("service",):
            text_value(object_value(data.get(key)).get("status"))
        for key in ("market_data", "trading", "qmt"):
            if key in data:
                text_value(object_value(data[key]).get("status"))
        if "market_data" in data:
            text_value(data["market_data"].get("backend"))
            if data["market_data"].get("xtquant_version") is not None:
                text_value(data["market_data"]["xtquant_version"])
        control = object_value(data.get("control"))
        if text_value(control.get("status")) not in {"AVAILABLE", "UNAVAILABLE"}:
            raise QMTProtocolError("Invalid control state")
        if control.get("lease_expires_at") is not None:
            timestamp_value(control["lease_expires_at"])
        if not isinstance(data.get("deployments"), list):
            raise QMTProtocolError("Invalid deployment list")
        for deployment in data["deployments"]:
            deployment_summary(deployment)
        if len({d["deployment_id"] for d in data["deployments"]}) != len(data["deployments"]):
            raise QMTProtocolError("Duplicate deployment summaries")
        return data

    @staticmethod
    def _session(data: dict) -> dict:
        text_value(data.get("session_id"))
        lease = data.get("lease_timeout_seconds")
        if type(lease) not in (int, float) or not 0 < lease < float("inf"):
            raise QMTProtocolError("Invalid session lease")
        return data

    async def open_control_session(self) -> dict:
        return self._session(await self._request("POST", "control-sessions"))

    async def renew_control_session(self, session_id: str, reconciled_deployments: list[dict] | None = None) -> dict:
        body = None if reconciled_deployments is None else {"reconciled_deployments": reconciled_deployments}
        data = self._session(
            await self._request("POST", f"control-sessions/{quote(session_id, safe='')}/renew", body=body)
        )
        if data["session_id"] != session_id:
            raise QMTProtocolError("Renewed a different control session")
        timestamp_value(data.get("expires_at"))
        return data

    async def close_control_session(self, session_id: str) -> None:
        data = await self._request("DELETE", f"control-sessions/{quote(session_id, safe='')}")
        if data.get("status") != "closed":
            raise QMTProtocolError("Invalid close response")

    async def stage_deployment(self, session_id: str, deployment_id: str, body: dict) -> dict:
        data = deployment_summary(
            await self._request("PUT", f"deployments/{quote(deployment_id, safe='')}", session_id=session_id, body=body)
        )
        if data["deployment_id"] != deployment_id:
            raise QMTProtocolError("Staged a different deployment")
        return data

    async def get_next_fact(self, session_id: str) -> dict | None:
        data = await self._request("GET", "facts/next", session_id=session_id)
        if set(data) != {"fact"}:
            raise QMTProtocolError("Invalid fact response")
        fact = data["fact"]
        if fact is None:
            return None
        fact = object_value(fact)
        if set(fact) != {"fact_id", "queue_sequence", "deployment_id", "fact_type", "external_fact_id", "payload"}:
            raise QMTProtocolError("Invalid fact envelope")
        text_value(fact["fact_id"])
        if sequence_value(fact["queue_sequence"]) == 0 or fact["fact_type"] != "TRADE_V1":
            raise QMTProtocolError("Invalid fact queue or type")
        trade = TradeFact.parse(fact["payload"])
        if fact["deployment_id"] != trade.deployment_id or fact["external_fact_id"] != trade.broker_trade_id:
            raise QMTProtocolError("Fact envelope identity mismatch")
        return fact

    async def ack_fact(self, session_id: str, fact_id: str, committed_ledger_sequence: int) -> dict:
        data = await self._request(
            "POST",
            f"facts/{quote(fact_id, safe='')}/ack",
            session_id=session_id,
            body={"committed_ledger_sequence": committed_ledger_sequence},
        )
        if (
            data.get("fact_id") != fact_id
            or data.get("status") != "ACKED"
            or sequence_value(data.get("committed_ledger_sequence")) != committed_ledger_sequence
        ):
            raise QMTProtocolError("Invalid ACK response")
        return data
