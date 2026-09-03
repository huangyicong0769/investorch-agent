from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from agents import RunContextWrapper
from agents.decorators import tool
from pydantic import BaseModel, ConfigDict

from investorch.application.portfolios import PortfolioMutationResult
from investorch.context import AgentContext
from investorch.portfolio import (
    CashAdjustment,
    CashFlow,
    CashTransfer,
    Income,
    InstrumentId,
    LedgerEntry,
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


class OpeningPositionInput(BaseModel):
    """Strict Agent input for one opening position."""

    model_config = ConfigDict(extra="forbid")

    code: str
    market: str
    quantity: str
    total_cost: str | None


@tool
async def list_portfolios(
    context: RunContextWrapper[AgentContext],
    include_archived: bool = False,
) -> dict[str, Any]:
    """List logical Portfolio metadata without loading holdings or Ledger history."""
    portfolios = await context.context.portfolios.list(include_archived=include_archived)
    return {"portfolios": [_serialize_portfolio(portfolio, include_timestamps=False) for portfolio in portfolios]}


@tool
async def get_portfolio(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
) -> dict[str, Any]:
    """Get one logical Portfolio's metadata and current projected holdings and cash."""
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
    """Get the newest bounded Portfolio Ledger entries in ascending append/audit order."""
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
    """Create durable logical Portfolio metadata; this does not add opening state."""
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
    """Update durable active Portfolio metadata without changing currency or lifecycle status."""
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
    """Archive and freeze a durable Portfolio until it is explicitly restored."""
    portfolio = await context.context.portfolios.archive(portfolio_id)
    return {"portfolio": _serialize_portfolio(portfolio, include_timestamps=True)}


@tool(needs_approval=True)
async def restore_portfolio(
    context: RunContextWrapper[AgentContext],
    portfolio_id: str,
) -> dict[str, Any]:
    """Restore an archived Portfolio so durable metadata and economic facts can change again."""
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
    """Initialize one empty active Portfolio with durable opening cash and positions exactly once."""
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
    commission: str = "0",
    tax: str = "0",
    other_fee: str = "0",
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Record an already-realized trade fact; this does not place a Broker order."""
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
    """Record external capital: a positive amount enters the Portfolio and a negative amount leaves it."""
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
    tax: str = "0",
    other_fee: str = "0",
    code: str | None = None,
    market: str | None = None,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Record realized income, optionally attributed to one instrument."""
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
    """Assert newly recognized position state; do not use this to erase a historically wrong Ledger entry."""
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
    """Assert newly recognized logical cash state in the Portfolio base currency."""
    result = await context.context.portfolios.adjust_cash(
        portfolio_id,
        resulting_amount=_parse_decimal(resulting_amount, "resulting_amount"),
        reason=reason,
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
