from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath


class PortfolioDomainError(ValueError):
    """Base error for invalid Portfolio domain data or operations."""


class InvalidLedgerError(PortfolioDomainError):
    """Raised when Ledger entries cannot form a valid Portfolio history."""


class InvalidVoidError(InvalidLedgerError):
    """Raised when a VOID relationship violates Ledger rules."""


class InsufficientPositionError(InvalidLedgerError):
    """Raised when an entry would create an unsupported short position."""


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioDomainError(f"{name} must not be empty")


def _require_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise PortfolioDomainError(f"{name} must be a datetime")


def _require_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise PortfolioDomainError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise PortfolioDomainError(f"{name} must be finite")
    return value


def _require_positive(value: object, name: str) -> None:
    if _require_decimal(value, name) <= 0:
        raise PortfolioDomainError(f"{name} must be positive")


def _require_non_negative(value: object, name: str) -> None:
    if _require_decimal(value, name) < 0:
        raise PortfolioDomainError(f"{name} must not be negative")


def _require_optional_non_negative(value: object, name: str) -> None:
    if value is not None:
        _require_non_negative(value, name)


@dataclass(frozen=True, slots=True)
class InstrumentId:
    code: str
    market: str

    def __post_init__(self) -> None:
        _require_text(self.code, "code")
        _require_text(self.market, "market")


class PortfolioStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StrategyBinding:
    source_path: str
    parameters: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.source_path, "source_path")
        posix_path = PurePosixPath(self.source_path)
        windows_path = PureWindowsPath(self.source_path)
        if posix_path.is_absolute() or windows_path.is_absolute() or ".." in posix_path.parts:
            raise PortfolioDomainError("source_path must be Workspace-relative")
        if not isinstance(self.parameters, dict) or any(not isinstance(key, str) for key in self.parameters):
            raise PortfolioDomainError("parameters must be a JSON-compatible object")
        try:
            json.dumps(self.parameters, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise PortfolioDomainError("parameters must be JSON-compatible") from exc
        object.__setattr__(self, "parameters", deepcopy(self.parameters))


@dataclass(frozen=True, slots=True)
class Portfolio:
    id: str
    name: str
    base_currency: str
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    status: PortfolioStatus = PortfolioStatus.ACTIVE
    strategy_binding: StrategyBinding | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.name, "name")
        _require_text(self.base_currency, "base_currency")
        _require_datetime(self.created_at, "created_at")
        _require_datetime(self.updated_at, "updated_at")
        if not isinstance(self.status, PortfolioStatus):
            raise PortfolioDomainError("status must be a PortfolioStatus")
        if self.strategy_binding is not None and not isinstance(self.strategy_binding, StrategyBinding):
            raise PortfolioDomainError("strategy_binding must be a StrategyBinding")


class LedgerEntryType(StrEnum):
    OPENING_POSITION = "OPENING_POSITION"
    OPENING_CASH = "OPENING_CASH"
    TRADE = "TRADE"
    CASH_FLOW = "CASH_FLOW"
    INCOME = "INCOME"
    TRANSFER = "TRANSFER"
    ADJUSTMENT = "ADJUSTMENT"
    VOID = "VOID"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class TransferDirection(StrEnum):
    IN = "IN"
    OUT = "OUT"


@dataclass(frozen=True, slots=True)
class OpeningPosition:
    instrument: InstrumentId
    quantity: Decimal
    total_cost: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise PortfolioDomainError("instrument must be an InstrumentId")
        _require_positive(self.quantity, "quantity")
        _require_optional_non_negative(self.total_cost, "total_cost")


@dataclass(frozen=True, slots=True)
class OpeningCash:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        _require_decimal(self.amount, "amount")


@dataclass(frozen=True, slots=True)
class Trade:
    instrument: InstrumentId
    side: TradeSide
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    other_fee: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise PortfolioDomainError("instrument must be an InstrumentId")
        if not isinstance(self.side, TradeSide):
            raise PortfolioDomainError("side must be a TradeSide")
        _require_positive(self.quantity, "quantity")
        _require_positive(self.price, "price")
        _require_non_negative(self.commission, "commission")
        _require_non_negative(self.tax, "tax")
        _require_non_negative(self.other_fee, "other_fee")


@dataclass(frozen=True, slots=True)
class CashFlow:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        _require_decimal(self.amount, "amount")


@dataclass(frozen=True, slots=True)
class Income:
    currency: str
    gross_amount: Decimal
    tax: Decimal = Decimal(0)
    other_fee: Decimal = Decimal(0)
    instrument: InstrumentId | None = None

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        _require_non_negative(self.gross_amount, "gross_amount")
        _require_non_negative(self.tax, "tax")
        _require_non_negative(self.other_fee, "other_fee")
        if self.instrument is not None and not isinstance(self.instrument, InstrumentId):
            raise PortfolioDomainError("instrument must be an InstrumentId")


@dataclass(frozen=True, slots=True)
class PositionTransfer:
    instrument: InstrumentId
    direction: TransferDirection
    quantity: Decimal
    transferred_cost: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise PortfolioDomainError("instrument must be an InstrumentId")
        if not isinstance(self.direction, TransferDirection):
            raise PortfolioDomainError("direction must be a TransferDirection")
        _require_positive(self.quantity, "quantity")
        _require_optional_non_negative(self.transferred_cost, "transferred_cost")


@dataclass(frozen=True, slots=True)
class CashTransfer:
    currency: str
    direction: TransferDirection
    amount: Decimal

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        if not isinstance(self.direction, TransferDirection):
            raise PortfolioDomainError("direction must be a TransferDirection")
        _require_positive(self.amount, "amount")


@dataclass(frozen=True, slots=True)
class PositionAdjustment:
    instrument: InstrumentId
    resulting_quantity: Decimal
    resulting_total_cost: Decimal | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise PortfolioDomainError("instrument must be an InstrumentId")
        _require_non_negative(self.resulting_quantity, "resulting_quantity")
        _require_optional_non_negative(self.resulting_total_cost, "resulting_total_cost")
        _require_text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class CashAdjustment:
    currency: str
    resulting_amount: Decimal
    reason: str

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        _require_decimal(self.resulting_amount, "resulting_amount")
        _require_text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class Void:
    target_entry_id: str
    reason: str

    def __post_init__(self) -> None:
        _require_text(self.target_entry_id, "target_entry_id")
        _require_text(self.reason, "reason")


type LedgerPayload = (
    OpeningPosition
    | OpeningCash
    | Trade
    | CashFlow
    | Income
    | PositionTransfer
    | CashTransfer
    | PositionAdjustment
    | CashAdjustment
    | Void
)


_PAYLOAD_TYPES: dict[LedgerEntryType, tuple[type[LedgerPayload], ...]] = {
    LedgerEntryType.OPENING_POSITION: (OpeningPosition,),
    LedgerEntryType.OPENING_CASH: (OpeningCash,),
    LedgerEntryType.TRADE: (Trade,),
    LedgerEntryType.CASH_FLOW: (CashFlow,),
    LedgerEntryType.INCOME: (Income,),
    LedgerEntryType.TRANSFER: (PositionTransfer, CashTransfer),
    LedgerEntryType.ADJUSTMENT: (PositionAdjustment, CashAdjustment),
    LedgerEntryType.VOID: (Void,),
}


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_id: str
    operation_id: str
    portfolio_id: str
    sequence: int
    entry_type: LedgerEntryType
    effective_at: datetime
    recorded_at: datetime
    source: str
    payload: LedgerPayload
    external_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.entry_id, "entry_id")
        _require_text(self.operation_id, "operation_id")
        _require_text(self.portfolio_id, "portfolio_id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence <= 0:
            raise PortfolioDomainError("sequence must be a positive integer")
        if not isinstance(self.entry_type, LedgerEntryType):
            raise PortfolioDomainError("entry_type must be a LedgerEntryType")
        _require_datetime(self.effective_at, "effective_at")
        _require_datetime(self.recorded_at, "recorded_at")
        _require_text(self.source, "source")
        if self.external_ref is not None:
            _require_text(self.external_ref, "external_ref")
        if not isinstance(self.payload, _PAYLOAD_TYPES[self.entry_type]):
            raise PortfolioDomainError(f"payload does not match {self.entry_type.value}")


@dataclass(frozen=True, slots=True)
class HoldingState:
    instrument: InstrumentId
    quantity: Decimal
    total_cost: Decimal | None

    @property
    def average_cost(self) -> Decimal | None:
        if self.total_cost is None or self.quantity == 0:
            return None
        return self.total_cost / self.quantity


@dataclass(frozen=True, slots=True)
class PortfolioState:
    portfolio_id: str
    holdings: dict[InstrumentId, HoldingState]
    cash: dict[str, Decimal]
