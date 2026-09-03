from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated, Any, Literal

from agents import RunContextWrapper
from agents.decorators import tool
from pydantic import BaseModel, ConfigDict, Field

from investorch.context import AgentContext
from investorch.portfolio import (
    CashAdjustment,
    CashFlow,
    CashTransfer,
    Income,
    InstrumentId,
    LedgerEntry,
    LedgerEntryType,
    OpeningCash,
    OpeningPosition,
    Portfolio,
    PortfolioState,
    PositionAdjustment,
    PositionTransfer,
    StrategyBinding,
    Trade,
    TradeSide,
    Void,
)
from investorch.portfolio.domain import LedgerPayload

if TYPE_CHECKING:
    from investorch.application.portfolios import PortfolioMutationResult


class OpeningPositionInput(BaseModel):
    """Strict Agent input for one opening position."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Instrument code.")
    market: str = Field(description="Instrument market identifier.")
    quantity: str = Field(description="Positive opening quantity as an exact decimal string.")
    total_cost: str | None = Field(
        description="Non-negative total cost as an exact decimal string, or null if unknown."
    )


class OpeningCashReplacementInput(BaseModel):
    """Strict replacement for opening cash."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["opening_cash"] = Field(description="Replacement payload type.")
    amount: str = Field(description="Opening cash as an exact decimal string.")


class OpeningPositionReplacementInput(BaseModel):
    """Strict replacement for an opening position."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["opening_position"] = Field(description="Replacement payload type.")
    code: str = Field(description="Instrument code.")
    market: str = Field(description="Instrument market identifier.")
    quantity: str = Field(description="Positive opening quantity as an exact decimal string.")
    total_cost: str | None = Field(description="Non-negative total cost, or null if unknown.")


class TradeReplacementInput(BaseModel):
    """Strict replacement for a realized trade."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["trade"] = Field(description="Replacement payload type.")
    code: str = Field(description="Instrument code.")
    market: str = Field(description="Instrument market identifier.")
    side: Literal["BUY", "SELL"] = Field(description="Realized trade side.")
    quantity: str = Field(description="Positive traded quantity as an exact decimal string.")
    price: str = Field(description="Positive unit price as an exact decimal string.")
    commission: str = Field(description="Explicitly grounded non-negative commission as an exact decimal string.")
    tax: str = Field(description="Explicitly grounded non-negative tax as an exact decimal string.")
    other_fee: str = Field(description="Explicitly grounded non-negative other fee as an exact decimal string.")


class CashFlowReplacementInput(BaseModel):
    """Strict replacement for an external cash flow."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["cash_flow"] = Field(description="Replacement payload type.")
    amount: str = Field(description="Signed cash flow as an exact decimal string.")


class IncomeReplacementInput(BaseModel):
    """Strict replacement for realized income."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["income"] = Field(description="Replacement payload type.")
    gross_amount: str = Field(description="Non-negative gross income as an exact decimal string.")
    tax: str = Field(description="Explicitly grounded non-negative tax as an exact decimal string.")
    other_fee: str = Field(description="Explicitly grounded non-negative other fee as an exact decimal string.")
    code: str | None = Field(default=None, description="Optional instrument code; supply with market.")
    market: str | None = Field(default=None, description="Optional instrument market; supply with code.")


class PositionAdjustmentReplacementInput(BaseModel):
    """Strict replacement for a position assertion."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["position_adjustment"] = Field(description="Replacement payload type.")
    code: str = Field(description="Instrument code.")
    market: str = Field(description="Instrument market identifier.")
    resulting_quantity: str = Field(description="Asserted non-negative quantity as an exact decimal string.")
    resulting_total_cost: str | None = Field(description="Asserted non-negative total cost, or null if unknown.")
    reason: str = Field(description="Reason for the replacement state assertion.")


class CashAdjustmentReplacementInput(BaseModel):
    """Strict replacement for a cash assertion."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["cash_adjustment"] = Field(description="Replacement payload type.")
    resulting_amount: str = Field(description="Asserted cash amount as an exact decimal string.")
    reason: str = Field(description="Reason for the replacement state assertion.")


CorrectionReplacement = Annotated[
    OpeningCashReplacementInput
    | OpeningPositionReplacementInput
    | TradeReplacementInput
    | CashFlowReplacementInput
    | IncomeReplacementInput
    | PositionAdjustmentReplacementInput
    | CashAdjustmentReplacementInput,
    Field(discriminator="type"),
]


@tool
async def list_portfolios(
    context: RunContextWrapper[AgentContext],
    include_archived: bool = False,
) -> dict[str, Any]:
    """List logical Portfolio metadata without holdings or Ledger history; returns metadata summaries.

    Args:
        include_archived: Include archived Portfolios when true. Defaults to false.

    Returns:
        Portfolio metadata summaries, excluding holdings and Ledger entries.
    """
    portfolios = await context.context.portfolios.list(include_archived=include_archived)
    return {"portfolios": [_serialize_portfolio(portfolio, include_timestamps=False) for portfolio in portfolios]}


@tool
async def get_portfolio(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
) -> dict[str, Any]:
    """Get one Portfolio; returns its metadata and current projected holdings and cash.

    Args:
        portfolio_id: Durable Portfolio identifier.

    Returns:
        Portfolio metadata and its current materialized logical state.
    """
    portfolio = await context.context.portfolios.get(portfolio_id)
    state = await context.context.portfolios.get_state(portfolio_id)
    return {
        "portfolio": _serialize_portfolio(portfolio, include_timestamps=True),
        "state": _serialize_state(state),
    }


@tool
async def get_portfolio_ledger(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Get recent Ledger history; returns newest entries in audit order with truncation metadata.

    Args:
        portfolio_id: Durable Portfolio identifier.
        limit: Maximum newest entries to return, from 1 through 200. Defaults to 50.

    Returns:
        Ledger entries plus total, returned, and has_older pagination metadata.
    """
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    ledger = await context.context.portfolios.list_ledger(portfolio_id)
    selected = ledger[-limit:]
    return {
        "portfolio_id": portfolio_id,
        "entries": [_serialize_ledger_entry(entry) for entry in selected],
        "returned": len(selected),
        "total": len(ledger),
        "has_older": len(selected) < len(ledger),
    }


@tool(needs_approval=True)
async def create_portfolio(
    context: RunContextWrapper[AgentContext],
    name: str,
    base_currency: str,
    description: str | None = None,
    strategy_source_path: str | None = None,
    strategy_parameters_json: str | None = None,
) -> dict[str, Any]:
    """Create durable Portfolio metadata without opening state; returns the created metadata.

    Args:
        name: Human-readable Portfolio name.
        base_currency: Single accounting currency for cash, costs, and transaction amounts.
        description: Optional Portfolio description.
        strategy_source_path: Optional strategy source path; supply together with strategy_parameters_json.
        strategy_parameters_json: Optional JSON object string of strategy parameters; supply with strategy_source_path.

    Returns:
        The created durable Portfolio metadata.
    """
    portfolio = await context.context.portfolios.create(
        name=name,
        base_currency=base_currency,
        description=description,
        strategy_binding=_strategy_binding(strategy_source_path, strategy_parameters_json),
    )
    return {"portfolio": _serialize_portfolio(portfolio, include_timestamps=True)}


@tool(needs_approval=True)
async def update_portfolio(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    name: str | None = None,
    description: str | None = None,
    clear_description: bool = False,
    strategy_source_path: str | None = None,
    strategy_parameters_json: str | None = None,
    clear_strategy_binding: bool = False,
) -> dict[str, Any]:
    """Update active Portfolio metadata without currency/status changes; returns the updated metadata.

    Args:
        portfolio_id: Durable Portfolio identifier.
        name: New name, or null to leave it unchanged.
        description: New description, or null to leave it unchanged unless clear_description is true.
        clear_description: Clear the description when true; cannot be combined with description.
        strategy_source_path: New strategy source path; supply with strategy_parameters_json.
        strategy_parameters_json: JSON object string of new strategy parameters; supply with strategy_source_path.
        clear_strategy_binding: Clear the strategy binding when true; cannot be combined with strategy fields.

    Returns:
        The updated durable Portfolio metadata.
    """
    if clear_description and description is not None:
        raise ValueError("description cannot be supplied when clear_description is true")
    if clear_strategy_binding and (strategy_source_path is not None or strategy_parameters_json is not None):
        raise ValueError("strategy binding fields cannot be supplied when clear_strategy_binding is true")

    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if clear_description:
        changes["description"] = None
    elif description is not None:
        changes["description"] = description
    if clear_strategy_binding:
        changes["strategy_binding"] = None
    elif strategy_source_path is not None or strategy_parameters_json is not None:
        changes["strategy_binding"] = _strategy_binding(strategy_source_path, strategy_parameters_json)

    portfolio = await context.context.portfolios.update_metadata(portfolio_id, **changes)
    return {"portfolio": _serialize_portfolio(portfolio, include_timestamps=True)}


@tool(needs_approval=True)
async def archive_portfolio(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
) -> dict[str, Any]:
    """Archive and freeze a durable Portfolio; returns the archived metadata.

    Args:
        portfolio_id: Durable Portfolio identifier.

    Returns:
        The archived Portfolio metadata.
    """
    portfolio = await context.context.portfolios.archive(portfolio_id)
    return {"portfolio": _serialize_portfolio(portfolio, include_timestamps=True)}


@tool(needs_approval=True)
async def restore_portfolio(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
) -> dict[str, Any]:
    """Restore an archived Portfolio for further mutation; returns the restored metadata.

    Args:
        portfolio_id: Durable Portfolio identifier.

    Returns:
        The restored Portfolio metadata.
    """
    portfolio = await context.context.portfolios.restore(portfolio_id)
    return {"portfolio": _serialize_portfolio(portfolio, include_timestamps=True)}


@tool(needs_approval=True)
async def initialize_portfolio(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    cash: str | None = None,
    positions: list[OpeningPositionInput] | None = None,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Initialize one empty Portfolio once; returns operation, entry, and resulting-state details.

    Args:
        portfolio_id: Durable Portfolio identifier.
        cash: Opening cash in the Portfolio base currency as an exact decimal string, or null to omit cash.
        positions: Opening positions with exact decimal strings, or null to omit positions.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for current initialization.

    Returns:
        Operation identifier, appended entry summaries, and the resulting Portfolio state.
    """
    opening_positions = tuple(
        OpeningPosition(
            _instrument(position.code, position.market),
            _parse_decimal(position.quantity, "positions.quantity"),
            _parse_optional_decimal(position.total_cost, "positions.total_cost"),
        )
        for position in positions or []
    )
    result = await context.context.portfolios.initialize(
        portfolio_id,
        cash=_parse_optional_decimal(cash, "cash"),
        positions=opening_positions,
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def record_portfolio_trade(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    code: str,
    market: str,
    side: Literal["BUY", "SELL"],
    quantity: str,
    price: str,
    commission: str,
    tax: str,
    other_fee: str,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Record a realized trade, not a Broker order; returns operation, entry, and resulting-state details.

    Args:
        portfolio_id: Durable Portfolio identifier.
        code: Instrument code.
        market: Instrument market identifier.
        side: Realized trade side, BUY or SELL.
        quantity: Positive traded quantity as an exact decimal string.
        price: Positive unit price in the Portfolio base currency as an exact decimal string.
        commission: Explicitly grounded non-negative commission as an exact decimal string; pass "0" for none.
        tax: Explicitly grounded non-negative tax as an exact decimal string; pass "0" for none.
        other_fee: Explicitly grounded non-negative other fee as an exact decimal string; pass "0" for none.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for a current event.

    Returns:
        Operation identifier, appended entry summaries, and the resulting Portfolio state.
    """
    result = await context.context.portfolios.record_trade(
        portfolio_id,
        instrument=_instrument(code, market),
        side=TradeSide(side),
        quantity=_parse_decimal(quantity, "quantity"),
        price=_parse_decimal(price, "price"),
        commission=_parse_decimal(commission, "commission"),
        tax=_parse_decimal(tax, "tax"),
        other_fee=_parse_decimal(other_fee, "other_fee"),
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def record_portfolio_cash_flow(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    amount: str,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Record external capital, not investment income; returns operation, entry, and resulting-state details.

    A positive amount enters the Portfolio and a negative amount leaves it.

    Args:
        portfolio_id: Durable Portfolio identifier.
        amount: Signed amount in the Portfolio base currency as an exact decimal string.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for a current cash flow.

    Returns:
        Operation identifier, appended entry summaries, and the resulting Portfolio state.
    """
    result = await context.context.portfolios.record_cash_flow(
        portfolio_id,
        amount=_parse_decimal(amount, "amount"),
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def record_portfolio_income(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    gross_amount: str,
    tax: str,
    other_fee: str,
    code: str | None = None,
    market: str | None = None,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Record investment-generated cash, not external capital; returns operation, entry, and state details.

    Income may optionally be attributed to one instrument.

    Args:
        portfolio_id: Durable Portfolio identifier.
        gross_amount: Non-negative gross income in the base currency as an exact decimal string.
        tax: Explicitly grounded non-negative tax as an exact decimal string; pass "0" for none.
        other_fee: Explicitly grounded non-negative other fee as an exact decimal string; pass "0" for none.
        code: Optional instrument code; supply together with market.
        market: Optional instrument market identifier; supply together with code.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for current income.

    Returns:
        Operation identifier, appended entry summaries, and the resulting Portfolio state.
    """
    result = await context.context.portfolios.record_income(
        portfolio_id,
        gross_amount=_parse_decimal(gross_amount, "gross_amount"),
        tax=_parse_decimal(tax, "tax"),
        other_fee=_parse_decimal(other_fee, "other_fee"),
        instrument=_optional_instrument(code, market),
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def adjust_portfolio_position(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    code: str,
    market: str,
    resulting_quantity: str,
    resulting_total_cost: str | None,
    reason: str,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Assert newly recognized position state; returns operation, entry, and resulting-state details.

    Do not use adjustment to erase a historically wrong Ledger entry; use correction instead.

    Args:
        portfolio_id: Durable Portfolio identifier.
        code: Instrument code.
        market: Instrument market identifier.
        resulting_quantity: Asserted non-negative quantity as an exact decimal string.
        resulting_total_cost: Asserted non-negative total cost as an exact decimal string, or null if unknown.
        reason: Human-readable reason for the state assertion.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for a current state assertion.

    Returns:
        Operation identifier, appended entry summaries, and the resulting Portfolio state.
    """
    result = await context.context.portfolios.adjust_position(
        portfolio_id,
        instrument=_instrument(code, market),
        resulting_quantity=_parse_decimal(resulting_quantity, "resulting_quantity"),
        resulting_total_cost=_parse_optional_decimal(resulting_total_cost, "resulting_total_cost"),
        reason=reason,
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def adjust_portfolio_cash(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    resulting_amount: str,
    reason: str,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Assert newly recognized cash state; returns operation, entry, and resulting-state details.

    The amount uses the Portfolio base currency. Use correction, not adjustment, for a wrong historical entry.

    Args:
        portfolio_id: Durable Portfolio identifier.
        resulting_amount: Asserted cash amount as an exact decimal string.
        reason: Human-readable reason for the state assertion.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for a current state assertion.

    Returns:
        Operation identifier, appended entry summaries, and the resulting Portfolio state.
    """
    result = await context.context.portfolios.adjust_cash(
        portfolio_id,
        resulting_amount=_parse_decimal(resulting_amount, "resulting_amount"),
        reason=reason,
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def correct_portfolio_entry(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
    target_entry_id: str,
    reason: str,
    replacement: CorrectionReplacement,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Append a VOID and replacement for a wrong entry; returns operation, entries, and resulting state.

    Args:
        portfolio_id: Durable Portfolio identifier.
        target_entry_id: Ledger entry to void and replace; TRANSFER entries are not supported.
        reason: Human-readable reason for correcting the historical fact.
        replacement: Strictly typed ordinary Ledger payload to append after the VOID entry.
        effective_at: Optional timezone-aware ISO-8601 timestamp for the replacement; null preserves the target time.

    Returns:
        Operation identifier, VOID and replacement entry summaries, and the resulting Portfolio state.
    """
    portfolio = await context.context.portfolios.get(portfolio_id)
    ledger = await context.context.portfolios.list_ledger(portfolio_id)
    target = next((entry for entry in ledger if entry.entry_id == target_entry_id), None)
    if target is not None and target.entry_type is LedgerEntryType.TRANSFER:
        raise ValueError(
            "Direct Agent correction of Portfolio TRANSFER entries is not supported; "
            "use a dedicated transfer correction workflow when available."
        )
    result = await context.context.portfolios.correct_entry(
        portfolio_id,
        target_entry_id=target_entry_id,
        replacement_payload=_parse_correction_replacement(replacement, portfolio.base_currency),
        effective_at=_parse_effective_at(effective_at),
        reason=reason,
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def transfer_portfolio_position(
    context: RunContextWrapper[AgentContext],
    source_portfolio_id: str,
    destination_portfolio_id: str,
    code: str,
    market: str,
    quantity: str,
    transferred_cost: str | None,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Atomically transfer a position without inferring cost; returns paired entries and both states.

    Args:
        source_portfolio_id: Durable identifier of the Portfolio giving the position.
        destination_portfolio_id: Durable identifier of the Portfolio receiving the position.
        code: Instrument code.
        market: Instrument market identifier.
        quantity: Positive transferred quantity as an exact decimal string.
        transferred_cost: Non-negative transferred total cost as an exact decimal string, or null if unknown.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for a current transfer.

    Returns:
        One operation identifier, paired transfer entry summaries, and both resulting Portfolio states.
    """
    result = await context.context.portfolios.transfer_position(
        source_portfolio_id=source_portfolio_id,
        destination_portfolio_id=destination_portfolio_id,
        instrument=_instrument(code, market),
        quantity=_parse_decimal(quantity, "quantity"),
        transferred_cost=_parse_optional_decimal(transferred_cost, "transferred_cost"),
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


@tool(needs_approval=True)
async def transfer_portfolio_cash(
    context: RunContextWrapper[AgentContext],
    source_portfolio_id: str,
    destination_portfolio_id: str,
    amount: str,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Atomically transfer same-currency cash without FX; returns paired entries and both states.

    Args:
        source_portfolio_id: Durable identifier of the Portfolio giving cash.
        destination_portfolio_id: Durable identifier of the Portfolio receiving cash.
        amount: Positive transfer amount in the shared base currency as an exact decimal string.
        effective_at: Optional timezone-aware ISO-8601 economic timestamp; null is only for a current transfer.

    Returns:
        One operation identifier, paired transfer entry summaries, and both resulting Portfolio states.
    """
    result = await context.context.portfolios.transfer_cash(
        source_portfolio_id=source_portfolio_id,
        destination_portfolio_id=destination_portfolio_id,
        amount=_parse_decimal(amount, "amount"),
        effective_at=_parse_effective_at(effective_at),
        source="agent",
        external_ref=None,
    )
    return _serialize_mutation_result(result)


def _serialize_portfolio(portfolio: Portfolio, *, include_timestamps: bool) -> dict[str, Any]:
    result = {
        "portfolio_id": portfolio.id,
        "name": portfolio.name,
        "description": portfolio.description,
        "status": portfolio.status.value,
        "base_currency": portfolio.base_currency,
        "strategy_binding": (
            None
            if portfolio.strategy_binding is None
            else {
                "source_path": portfolio.strategy_binding.source_path,
                "parameters": portfolio.strategy_binding.parameters,
            }
        ),
    }
    if include_timestamps:
        result["created_at"] = portfolio.created_at.isoformat()
        result["updated_at"] = portfolio.updated_at.isoformat()
    return result


def _serialize_state(state: PortfolioState) -> dict[str, Any]:
    holdings = []
    for instrument, holding in sorted(
        state.holdings.items(),
        key=lambda item: (item[0].code, item[0].market),
    ):
        holdings.append(
            {
                "instrument": _serialize_instrument(instrument),
                "quantity": str(holding.quantity),
                "total_cost": None if holding.total_cost is None else str(holding.total_cost),
                "average_cost": None if holding.average_cost is None else str(holding.average_cost),
            }
        )
    return {
        "portfolio_id": state.portfolio_id,
        "cash": {currency: str(state.cash[currency]) for currency in sorted(state.cash)},
        "holdings": holdings,
    }


def _serialize_ledger_entry(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "operation_id": entry.operation_id,
        "sequence": entry.sequence,
        "entry_type": entry.entry_type.value,
        "effective_at": entry.effective_at.isoformat(),
        "recorded_at": entry.recorded_at.isoformat(),
        "source": entry.source,
        "external_ref": entry.external_ref,
        "payload": _serialize_payload(entry.payload),
    }


def _serialize_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, OpeningPosition):
        return {
            "instrument": _serialize_instrument(payload.instrument),
            "quantity": str(payload.quantity),
            "total_cost": None if payload.total_cost is None else str(payload.total_cost),
        }
    if isinstance(payload, OpeningCash):
        return {"currency": payload.currency, "amount": str(payload.amount)}
    if isinstance(payload, Trade):
        return {
            "instrument": _serialize_instrument(payload.instrument),
            "side": payload.side.value,
            "quantity": str(payload.quantity),
            "price": str(payload.price),
            "commission": str(payload.commission),
            "tax": str(payload.tax),
            "other_fee": str(payload.other_fee),
        }
    if isinstance(payload, CashFlow):
        return {"currency": payload.currency, "amount": str(payload.amount)}
    if isinstance(payload, Income):
        return {
            "currency": payload.currency,
            "gross_amount": str(payload.gross_amount),
            "tax": str(payload.tax),
            "other_fee": str(payload.other_fee),
            "instrument": None if payload.instrument is None else _serialize_instrument(payload.instrument),
        }
    if isinstance(payload, PositionTransfer):
        return {
            "instrument": _serialize_instrument(payload.instrument),
            "direction": payload.direction.value,
            "quantity": str(payload.quantity),
            "transferred_cost": None if payload.transferred_cost is None else str(payload.transferred_cost),
        }
    if isinstance(payload, CashTransfer):
        return {
            "currency": payload.currency,
            "direction": payload.direction.value,
            "amount": str(payload.amount),
        }
    if isinstance(payload, PositionAdjustment):
        return {
            "instrument": _serialize_instrument(payload.instrument),
            "resulting_quantity": str(payload.resulting_quantity),
            "resulting_total_cost": (
                None if payload.resulting_total_cost is None else str(payload.resulting_total_cost)
            ),
            "reason": payload.reason,
        }
    if isinstance(payload, CashAdjustment):
        return {
            "currency": payload.currency,
            "resulting_amount": str(payload.resulting_amount),
            "reason": payload.reason,
        }
    if isinstance(payload, Void):
        return {"target_entry_id": payload.target_entry_id, "reason": payload.reason}
    raise TypeError(f"unsupported Portfolio Ledger payload: {type(payload).__name__}")


def _serialize_instrument(instrument: InstrumentId) -> dict[str, str]:
    return {"code": instrument.code, "market": instrument.market}


def _serialize_mutation_result(result: PortfolioMutationResult) -> dict[str, Any]:
    return {
        "operation_id": result.operation_id,
        "entries": [
            {
                "entry_id": entry.entry_id,
                "portfolio_id": entry.portfolio_id,
                "sequence": entry.sequence,
                "entry_type": entry.entry_type.value,
                "effective_at": entry.effective_at.isoformat(),
                "recorded_at": entry.recorded_at.isoformat(),
            }
            for entry in result.entries
        ],
        "states": {
            portfolio_id: _serialize_state(result.states[portfolio_id]) for portfolio_id in sorted(result.states)
        },
    }


def _parse_decimal(value: str, field: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a valid decimal string")
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a valid decimal string") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite decimal string")
    return parsed


def _parse_optional_decimal(value: str | None, field: str) -> Decimal | None:
    return None if value is None else _parse_decimal(value, field)


def _instrument(code: str, market: str) -> InstrumentId:
    return InstrumentId(code, market)


def _optional_instrument(code: str | None, market: str | None) -> InstrumentId | None:
    if code is None and market is None:
        return None
    if code is None or market is None:
        raise ValueError("code and market must be supplied together")
    return _instrument(code, market)


def _parse_correction_replacement(replacement: CorrectionReplacement, base_currency: str) -> LedgerPayload:
    if isinstance(replacement, OpeningCashReplacementInput):
        return OpeningCash(base_currency, _parse_decimal(replacement.amount, "replacement.amount"))
    if isinstance(replacement, OpeningPositionReplacementInput):
        return OpeningPosition(
            _instrument(replacement.code, replacement.market),
            _parse_decimal(replacement.quantity, "replacement.quantity"),
            _parse_optional_decimal(replacement.total_cost, "replacement.total_cost"),
        )
    if isinstance(replacement, TradeReplacementInput):
        return Trade(
            _instrument(replacement.code, replacement.market),
            TradeSide(replacement.side),
            _parse_decimal(replacement.quantity, "replacement.quantity"),
            _parse_decimal(replacement.price, "replacement.price"),
            _parse_decimal(replacement.commission, "replacement.commission"),
            _parse_decimal(replacement.tax, "replacement.tax"),
            _parse_decimal(replacement.other_fee, "replacement.other_fee"),
        )
    if isinstance(replacement, CashFlowReplacementInput):
        return CashFlow(base_currency, _parse_decimal(replacement.amount, "replacement.amount"))
    if isinstance(replacement, IncomeReplacementInput):
        return Income(
            base_currency,
            _parse_decimal(replacement.gross_amount, "replacement.gross_amount"),
            _parse_decimal(replacement.tax, "replacement.tax"),
            _parse_decimal(replacement.other_fee, "replacement.other_fee"),
            _optional_instrument(replacement.code, replacement.market),
        )
    if isinstance(replacement, PositionAdjustmentReplacementInput):
        return PositionAdjustment(
            _instrument(replacement.code, replacement.market),
            _parse_decimal(replacement.resulting_quantity, "replacement.resulting_quantity"),
            _parse_optional_decimal(replacement.resulting_total_cost, "replacement.resulting_total_cost"),
            replacement.reason,
        )
    if isinstance(replacement, CashAdjustmentReplacementInput):
        return CashAdjustment(
            base_currency,
            _parse_decimal(replacement.resulting_amount, "replacement.resulting_amount"),
            replacement.reason,
        )
    raise TypeError(f"unsupported correction replacement: {type(replacement).__name__}")


def _parse_effective_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("effective_at must be an ISO-8601 timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("effective_at must be an ISO-8601 timezone-aware timestamp")
    return parsed


def _strategy_binding(source_path: str | None, parameters_json: str | None) -> StrategyBinding | None:
    if source_path is None and parameters_json is None:
        return None
    if source_path is None or parameters_json is None:
        raise ValueError("strategy_source_path and strategy_parameters_json must be supplied together")
    try:
        parameters = json.loads(parameters_json)
    except json.JSONDecodeError as exc:
        raise ValueError("strategy_parameters_json must be a valid JSON object") from exc
    if not isinstance(parameters, dict):
        raise ValueError("strategy_parameters_json must be a valid JSON object")
    return StrategyBinding(source_path, parameters)
