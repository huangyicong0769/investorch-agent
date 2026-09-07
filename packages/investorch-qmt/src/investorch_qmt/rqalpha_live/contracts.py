"""Independent consumer of the exact Core bootstrap V1 wire contract."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class BootstrapPosition:
    code: str
    market: str
    quantity: Decimal


@dataclass(frozen=True)
class RQAlphaLiveBootstrapSnapshot:
    deployment_id: str
    portfolio_id: str
    broker_account_id: str
    ledger_sequence: int
    generated_at: datetime
    base_currency: str
    cash: Decimal
    positions: tuple[BootstrapPosition, ...]

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> "RQAlphaLiveBootstrapSnapshot":
        expected = {
            "schema_version",
            "deployment_id",
            "portfolio_id",
            "broker_account_id",
            "ledger_sequence",
            "generated_at",
            "base_currency",
            "cash",
            "positions",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("bootstrap snapshot must contain exactly the V1 fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("bootstrap schema_version must be integer 1")
        sequence = value["ledger_sequence"]
        if type(sequence) is not int or sequence < 0:
            raise ValueError("ledger_sequence must be a nonnegative integer")
        timestamp = datetime.fromisoformat(_text(value["generated_at"], "generated_at"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("generated_at must include timezone")
        if not isinstance(value["positions"], list):
            raise ValueError("positions must be an array")
        positions = []
        identities = set()
        for item in value["positions"]:
            if not isinstance(item, dict) or set(item) != {"code", "market", "quantity"}:
                raise ValueError("position must contain exactly code, market, quantity")
            code, market = _text(item["code"], "code"), _text(item["market"], "market")
            if (code, market) in identities:
                raise ValueError("duplicate position identity")
            identities.add((code, market))
            positions.append(BootstrapPosition(code, market, _decimal(item["quantity"], "quantity")))
        return cls(
            deployment_id=_text(value["deployment_id"], "deployment_id"),
            portfolio_id=_text(value["portfolio_id"], "portfolio_id"),
            broker_account_id=_text(value["broker_account_id"], "broker_account_id"),
            ledger_sequence=sequence,
            generated_at=timestamp,
            base_currency=_text(value["base_currency"], "base_currency"),
            cash=_decimal(value["cash"], "cash"),
            positions=tuple(positions),
        )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field} must be a nonempty string without surrounding whitespace")
    return value


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value) is None:
        raise ValueError(f"{field} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal string") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result
