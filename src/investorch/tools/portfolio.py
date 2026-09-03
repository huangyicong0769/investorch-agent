from __future__ import annotations

from typing import Any

from agents import RunContextWrapper
from agents.decorators import tool

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
    Trade,
    Void,
)


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
