"""Independent Core parser for execution-node wire data."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from investorch.portfolio.domain import InstrumentId, TradeSide

from .errors import QMTProtocolError


def object_value(value: Any) -> dict:
    if not isinstance(value, dict):
        raise QMTProtocolError("Expected a JSON object")
    return value


def text_value(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise QMTProtocolError("Expected a nonempty trimmed string")
    return value


def sequence_value(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise QMTProtocolError("Expected a nonnegative integer")
    return value


def timestamp_value(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(text_value(value))
        if result.utcoffset() is None:
            raise ValueError("Timezone required")
        return result
    except ValueError as exc:
        raise QMTProtocolError("Expected a timezone-aware timestamp") from exc


def deployment_summary(value: Any) -> dict:
    value = object_value(value)
    for key in ("deployment_id", "portfolio_id", "broker_account_id"):
        text_value(value.get(key))
    if text_value(value.get("status")) not in {"STAGED", "RUNNING", "STOPPED", "FAILED"}:
        raise QMTProtocolError("Invalid remote deployment status")
    for key in ("acked_core_sequence", "pending_fact_count"):
        sequence_value(value.get(key))
    if text_value(value.get("portfolio_sync")) not in {"UNKNOWN", "SYNCED", "COMMIT_PENDING", "DESYNCED"}:
        raise QMTProtocolError("Invalid portfolio synchronization state")
    if value.get("failure_reason") is not None:
        text_value(value["failure_reason"])
    return value


@dataclass(frozen=True, slots=True)
class TradeFact:
    deployment_id: str
    broker_trade_id: str
    instrument: InstrumentId
    side: TradeSide
    quantity: Decimal
    price: Decimal
    commission: Decimal
    tax: Decimal
    other_fee: Decimal
    effective_at: datetime

    @classmethod
    def parse(cls, value: Any) -> "TradeFact":
        value = object_value(value)
        fields = {
            "schema_version",
            "deployment_id",
            "broker_trade_id",
            "instrument",
            "side",
            "quantity",
            "price",
            "commission",
            "tax",
            "other_fee",
            "effective_at",
        }
        if set(value) != fields or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise QMTProtocolError("Unsupported or malformed TRADE_V1")
        instrument = object_value(value["instrument"])
        if set(instrument) != {"code", "market"}:
            raise QMTProtocolError("Invalid instrument fields")
        amounts = {}
        for name in ("quantity", "price", "commission", "tax", "other_fee"):
            raw = text_value(value[name])
            try:
                amount = Decimal(raw)
            except InvalidOperation as exc:
                raise QMTProtocolError("Invalid decimal string") from exc
            if not amount.is_finite() or amount < 0 or (name in {"quantity", "price"} and amount == 0):
                raise QMTProtocolError("Invalid trade amount")
            amounts[name] = amount
        try:
            side = TradeSide(value["side"])
            parsed_instrument = InstrumentId(text_value(instrument["code"]), text_value(instrument["market"]))
        except (ValueError, TypeError) as exc:
            raise QMTProtocolError("Invalid trade instrument or side") from exc
        return cls(
            text_value(value["deployment_id"]),
            text_value(value["broker_trade_id"]),
            parsed_instrument,
            side,
            **amounts,
            effective_at=timestamp_value(value["effective_at"]),
        )
