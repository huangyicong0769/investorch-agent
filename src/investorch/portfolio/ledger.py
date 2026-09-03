from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from investorch.portfolio.domain import (
    CashAdjustment,
    CashFlow,
    CashTransfer,
    CurrencyMismatchError,
    HoldingState,
    Income,
    InsufficientPositionError,
    InvalidLedgerError,
    InvalidVoidError,
    LedgerEntry,
    LedgerEntryType,
    OpeningCash,
    OpeningPosition,
    Portfolio,
    PortfolioState,
    PositionAdjustment,
    PositionTransfer,
    Trade,
    TradeSide,
    TransferDirection,
    Void,
)


def project_portfolio(portfolio: Portfolio, entries: Iterable[LedgerEntry]) -> PortfolioState:
    """Rebuild a Portfolio's current Holdings and logical Cash from its Ledger."""
    ledger = tuple(entries)
    _validate_entry_types(ledger)
    voided_entry_ids = _resolve_voids(ledger)
    _validate_ledger_context(portfolio, ledger)
    active_entries = sorted(
        (
            entry
            for entry in ledger
            if entry.entry_id not in voided_entry_ids and entry.entry_type is not LedgerEntryType.VOID
        ),
        key=lambda entry: (entry.effective_at, entry.sequence),
    )

    holdings: dict = {}
    cash: dict[str, Decimal] = {}
    for entry in active_entries:
        _apply_entry(portfolio, entry, holdings, cash)

    return PortfolioState(portfolio_id=portfolio.id, holdings=dict(holdings), cash=dict(cash))


def _validate_entry_types(ledger: tuple[LedgerEntry, ...]) -> None:
    if any(not isinstance(entry, LedgerEntry) for entry in ledger):
        raise InvalidLedgerError("entries must contain only LedgerEntry values")


def _validate_ledger_context(portfolio: Portfolio, ledger: tuple[LedgerEntry, ...]) -> None:
    for entry in ledger:
        if entry.portfolio_id != portfolio.id:
            raise InvalidLedgerError(f"entry {entry.entry_id} belongs to a different Portfolio")

        currency = _explicit_currency(entry)
        if currency is not None and currency != portfolio.base_currency:
            raise CurrencyMismatchError(
                f"entry {entry.entry_id} uses {currency}, expected Portfolio base currency {portfolio.base_currency}"
            )


def _explicit_currency(entry: LedgerEntry) -> str | None:
    payload = entry.payload
    if isinstance(payload, OpeningCash | CashFlow | Income | CashTransfer | CashAdjustment):
        return payload.currency
    return None


def _resolve_voids(ledger: tuple[LedgerEntry, ...]) -> set[str]:
    entries_by_id: dict[str, LedgerEntry] = {}
    sequences: set[int] = set()
    voided_entry_ids: set[str] = set()

    for entry in sorted(ledger, key=lambda candidate: candidate.sequence):
        if entry.entry_id in entries_by_id:
            raise InvalidLedgerError(f"duplicate entry_id: {entry.entry_id}")
        if entry.sequence in sequences:
            raise InvalidLedgerError(f"duplicate sequence: {entry.sequence}")

        if isinstance(entry.payload, Void):
            target = entries_by_id.get(entry.payload.target_entry_id)
            if target is None:
                raise InvalidVoidError(f"VOID {entry.entry_id} must target an existing earlier entry")
            if target.entry_type is LedgerEntryType.VOID:
                raise InvalidVoidError(f"VOID {entry.entry_id} cannot target another VOID")
            if target.portfolio_id != entry.portfolio_id:
                raise InvalidVoidError(f"VOID {entry.entry_id} must target an entry in the same Portfolio")
            if target.entry_id in voided_entry_ids:
                raise InvalidVoidError(f"entry {target.entry_id} is already voided")
            voided_entry_ids.add(target.entry_id)

        entries_by_id[entry.entry_id] = entry
        sequences.add(entry.sequence)

    return voided_entry_ids


def _apply_entry(
    portfolio: Portfolio,
    entry: LedgerEntry,
    holdings: dict,
    cash: dict[str, Decimal],
) -> None:
    payload = entry.payload
    if isinstance(payload, OpeningPosition):
        _add_position(holdings, payload.instrument, payload.quantity, payload.total_cost)
    elif isinstance(payload, OpeningCash | CashFlow):
        _add_cash(cash, payload.currency, payload.amount)
    elif isinstance(payload, Trade):
        _apply_trade(portfolio, holdings, cash, payload)
    elif isinstance(payload, Income):
        _add_cash(cash, payload.currency, payload.gross_amount - payload.tax - payload.other_fee)
    elif isinstance(payload, PositionTransfer):
        _apply_position_transfer(holdings, payload)
    elif isinstance(payload, CashTransfer):
        sign = Decimal(1) if payload.direction is TransferDirection.IN else Decimal(-1)
        _add_cash(cash, payload.currency, sign * payload.amount)
    elif isinstance(payload, PositionAdjustment):
        if payload.resulting_quantity == 0:
            holdings.pop(payload.instrument, None)
        else:
            holdings[payload.instrument] = HoldingState(
                instrument=payload.instrument,
                quantity=payload.resulting_quantity,
                total_cost=payload.resulting_total_cost,
            )
    elif isinstance(payload, CashAdjustment):
        cash[payload.currency] = payload.resulting_amount


def _apply_trade(portfolio: Portfolio, holdings: dict, cash: dict[str, Decimal], trade: Trade) -> None:
    gross = trade.quantity * trade.price
    fees = trade.commission + trade.tax + trade.other_fee
    if trade.side is TradeSide.BUY:
        _add_position(holdings, trade.instrument, trade.quantity, gross + fees)
        _add_cash(cash, portfolio.base_currency, -(gross + fees))
        return

    _remove_average_cost_position(holdings, trade.instrument, trade.quantity)
    _add_cash(cash, portfolio.base_currency, gross - fees)


def _add_position(holdings: dict, instrument, quantity: Decimal, added_cost: Decimal | None) -> None:
    current = holdings.get(instrument)
    if current is None:
        holdings[instrument] = HoldingState(instrument=instrument, quantity=quantity, total_cost=added_cost)
        return

    total_cost = None if current.total_cost is None or added_cost is None else current.total_cost + added_cost
    holdings[instrument] = HoldingState(
        instrument=instrument,
        quantity=current.quantity + quantity,
        total_cost=total_cost,
    )


def _remove_average_cost_position(holdings: dict, instrument, quantity: Decimal) -> None:
    current = _require_sufficient_position(holdings, instrument, quantity)
    if current.quantity == quantity:
        del holdings[instrument]
        return

    remaining_cost = None
    if current.total_cost is not None:
        remaining_cost = current.total_cost - current.average_cost * quantity
    holdings[instrument] = HoldingState(
        instrument=instrument,
        quantity=current.quantity - quantity,
        total_cost=remaining_cost,
    )


def _apply_position_transfer(holdings: dict, transfer: PositionTransfer) -> None:
    if transfer.direction is TransferDirection.IN:
        _add_position(holdings, transfer.instrument, transfer.quantity, transfer.transferred_cost)
        return

    current = _require_sufficient_position(holdings, transfer.instrument, transfer.quantity)
    if current.quantity == transfer.quantity:
        if (
            current.total_cost is not None
            and transfer.transferred_cost is not None
            and current.total_cost != transfer.transferred_cost
        ):
            raise InvalidLedgerError("a full position transfer must move the full known cost")
        del holdings[transfer.instrument]
        return

    remaining_cost = None
    if current.total_cost is not None and transfer.transferred_cost is not None:
        if transfer.transferred_cost > current.total_cost:
            raise InvalidLedgerError("transferred cost exceeds the Holding total cost")
        remaining_cost = current.total_cost - transfer.transferred_cost
    holdings[transfer.instrument] = HoldingState(
        instrument=transfer.instrument,
        quantity=current.quantity - transfer.quantity,
        total_cost=remaining_cost,
    )


def _require_sufficient_position(holdings: dict, instrument, quantity: Decimal) -> HoldingState:
    current = holdings.get(instrument)
    if current is None or current.quantity < quantity:
        raise InsufficientPositionError(f"insufficient position for {instrument.code} on {instrument.market}")
    return current


def _add_cash(cash: dict[str, Decimal], currency: str, amount: Decimal) -> None:
    cash[currency] = cash.get(currency, Decimal(0)) + amount
