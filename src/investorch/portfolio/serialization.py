from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from investorch.portfolio.domain import (
    CashAdjustment,
    CashFlow,
    CashTransfer,
    Income,
    InstrumentId,
    LedgerEntryType,
    LedgerPayload,
    OpeningCash,
    OpeningPosition,
    PositionAdjustment,
    PositionTransfer,
    Trade,
    TradeSide,
    TransferDirection,
    Void,
)
from investorch.portfolio.schema import PortfolioDataError


def serialize_ledger_payload(payload: LedgerPayload) -> str:
    """Encode one A0 Ledger payload in the current canonical JSON format."""
    if isinstance(payload, OpeningPosition):
        value = {
            "instrument": _serialize_instrument(payload.instrument),
            "quantity": str(payload.quantity),
            "total_cost": _serialize_optional_decimal(payload.total_cost),
        }
    elif isinstance(payload, OpeningCash):
        value = {"amount": str(payload.amount), "currency": payload.currency}
    elif isinstance(payload, Trade):
        value = {
            "commission": str(payload.commission),
            "instrument": _serialize_instrument(payload.instrument),
            "other_fee": str(payload.other_fee),
            "price": str(payload.price),
            "quantity": str(payload.quantity),
            "side": payload.side.value,
            "tax": str(payload.tax),
        }
    elif isinstance(payload, CashFlow):
        value = {"amount": str(payload.amount), "currency": payload.currency}
    elif isinstance(payload, Income):
        value = {
            "currency": payload.currency,
            "gross_amount": str(payload.gross_amount),
            "instrument": None if payload.instrument is None else _serialize_instrument(payload.instrument),
            "other_fee": str(payload.other_fee),
            "tax": str(payload.tax),
        }
    elif isinstance(payload, PositionTransfer):
        value = {
            "direction": payload.direction.value,
            "instrument": _serialize_instrument(payload.instrument),
            "kind": "position",
            "quantity": str(payload.quantity),
            "transferred_cost": _serialize_optional_decimal(payload.transferred_cost),
        }
    elif isinstance(payload, CashTransfer):
        value = {
            "amount": str(payload.amount),
            "currency": payload.currency,
            "direction": payload.direction.value,
            "kind": "cash",
        }
    elif isinstance(payload, PositionAdjustment):
        value = {
            "instrument": _serialize_instrument(payload.instrument),
            "kind": "position",
            "reason": payload.reason,
            "resulting_quantity": str(payload.resulting_quantity),
            "resulting_total_cost": _serialize_optional_decimal(payload.resulting_total_cost),
        }
    elif isinstance(payload, CashAdjustment):
        value = {
            "currency": payload.currency,
            "kind": "cash",
            "reason": payload.reason,
            "resulting_amount": str(payload.resulting_amount),
        }
    elif isinstance(payload, Void):
        value = {"reason": payload.reason, "target_entry_id": payload.target_entry_id}
    else:
        raise PortfolioDataError(f"unsupported Ledger payload type: {type(payload).__name__}")

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def deserialize_ledger_payload(
    entry_type: LedgerEntryType,
    payload_json: str,
    *,
    entry_id: str,
) -> LedgerPayload:
    """Decode and validate one canonical Ledger payload."""
    try:
        value = json.loads(payload_json)
        if not isinstance(value, dict):
            raise TypeError("payload must be a JSON object")
        return _deserialize_payload(entry_type, value)
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        entry_type_name = entry_type.value if isinstance(entry_type, LedgerEntryType) else str(entry_type)
        raise PortfolioDataError(f"invalid payload for entry {entry_id} ({entry_type_name}): {exc}") from exc


def serialize_strategy_parameters(parameters: dict) -> str:
    """Encode StrategyBinding parameters deterministically."""
    return json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def deserialize_strategy_parameters(parameters_json: str, *, portfolio_id: str) -> dict:
    """Decode StrategyBinding parameters and require a JSON object."""
    try:
        parameters = json.loads(parameters_json)
        if not isinstance(parameters, dict):
            raise TypeError("strategy parameters must be a JSON object")
        return parameters
    except (TypeError, ValueError) as exc:
        raise PortfolioDataError(f"invalid strategy parameters for Portfolio {portfolio_id}: {exc}") from exc


def _deserialize_payload(entry_type: LedgerEntryType, value: dict) -> LedgerPayload:
    if entry_type is LedgerEntryType.OPENING_POSITION:
        return OpeningPosition(
            _deserialize_instrument(value["instrument"]),
            _deserialize_decimal(value["quantity"]),
            _deserialize_optional_decimal(value["total_cost"]),
        )
    if entry_type is LedgerEntryType.OPENING_CASH:
        return OpeningCash(value["currency"], _deserialize_decimal(value["amount"]))
    if entry_type is LedgerEntryType.TRADE:
        return Trade(
            _deserialize_instrument(value["instrument"]),
            TradeSide(value["side"]),
            _deserialize_decimal(value["quantity"]),
            _deserialize_decimal(value["price"]),
            _deserialize_decimal(value["commission"]),
            _deserialize_decimal(value["tax"]),
            _deserialize_decimal(value["other_fee"]),
        )
    if entry_type is LedgerEntryType.CASH_FLOW:
        return CashFlow(value["currency"], _deserialize_decimal(value["amount"]))
    if entry_type is LedgerEntryType.INCOME:
        instrument = value["instrument"]
        return Income(
            value["currency"],
            _deserialize_decimal(value["gross_amount"]),
            _deserialize_decimal(value["tax"]),
            _deserialize_decimal(value["other_fee"]),
            None if instrument is None else _deserialize_instrument(instrument),
        )
    if entry_type is LedgerEntryType.TRANSFER:
        return _deserialize_transfer(value)
    if entry_type is LedgerEntryType.ADJUSTMENT:
        return _deserialize_adjustment(value)
    if entry_type is LedgerEntryType.VOID:
        return Void(value["target_entry_id"], value["reason"])
    raise ValueError(f"unsupported Ledger entry type: {entry_type}")


def _deserialize_transfer(value: dict) -> PositionTransfer | CashTransfer:
    kind = value["kind"]
    if kind == "position":
        return PositionTransfer(
            _deserialize_instrument(value["instrument"]),
            TransferDirection(value["direction"]),
            _deserialize_decimal(value["quantity"]),
            _deserialize_optional_decimal(value["transferred_cost"]),
        )
    if kind == "cash":
        return CashTransfer(
            value["currency"], TransferDirection(value["direction"]), _deserialize_decimal(value["amount"])
        )
    raise ValueError(f"unsupported TRANSFER kind: {kind}")


def _deserialize_adjustment(value: dict) -> PositionAdjustment | CashAdjustment:
    kind = value["kind"]
    if kind == "position":
        return PositionAdjustment(
            _deserialize_instrument(value["instrument"]),
            _deserialize_decimal(value["resulting_quantity"]),
            _deserialize_optional_decimal(value["resulting_total_cost"]),
            value["reason"],
        )
    if kind == "cash":
        return CashAdjustment(value["currency"], _deserialize_decimal(value["resulting_amount"]), value["reason"])
    raise ValueError(f"unsupported ADJUSTMENT kind: {kind}")


def _serialize_instrument(instrument: InstrumentId) -> dict[str, str]:
    return {"code": instrument.code, "market": instrument.market}


def _deserialize_instrument(value: object) -> InstrumentId:
    if not isinstance(value, dict):
        raise TypeError("instrument must be a JSON object")
    return InstrumentId(value["code"], value["market"])


def _serialize_optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _deserialize_decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise TypeError("Decimal value must be a JSON string")
    return Decimal(value)


def _deserialize_optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _deserialize_decimal(value)
