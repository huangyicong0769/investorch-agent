from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from investorch.config import AppConfig
from investorch.storage import add_session_related_portfolio_ids, get_session_related_portfolio_ids

from .sessions import SessionNotFoundError

logger = logging.getLogger(__name__)

_PORTFOLIO_OBJECT_TOOLS = frozenset(
    {
        "create_portfolio",
        "update_portfolio",
        "archive_portfolio",
        "restore_portfolio",
        "get_portfolio",
    }
)
_PORTFOLIO_LEDGER_TOOLS = frozenset({"get_portfolio_ledger"})
_PORTFOLIO_OPERATION_TOOLS = frozenset(
    {
        "initialize_portfolio",
        "record_portfolio_trade",
        "record_portfolio_cash_flow",
        "record_portfolio_income",
        "adjust_portfolio_position",
        "adjust_portfolio_cash",
        "correct_portfolio_entry",
        "transfer_portfolio_position",
        "transfer_portfolio_cash",
    }
)


class PortfolioContextOperations:
    """Maintain durable Portfolio identities related through structured Agent interactions."""

    def __init__(self, *, config: AppConfig) -> None:
        self._config = config

    async def related_ids(self, session_id: str) -> tuple[str, ...]:
        try:
            return await asyncio.to_thread(
                get_session_related_portfolio_ids,
                self._config.sessions_db,
                session_id,
            )
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    async def add_related(self, session_id: str, portfolio_ids: tuple[str, ...]) -> tuple[str, ...]:
        try:
            return await asyncio.to_thread(
                add_session_related_portfolio_ids,
                self._config.sessions_db,
                session_id,
                portfolio_ids,
            )
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    async def observe_successful_tool(
        self,
        session_id: str,
        run_id: str,
        tool_name: str,
        result: object,
    ) -> None:
        portfolio_ids = _related_portfolio_ids(tool_name, result)
        if not portfolio_ids:
            return
        try:
            await self.add_related(session_id, portfolio_ids)
        except Exception:
            logger.exception(
                "Failed to relate successful Portfolio tool result session=%s run=%s tool=%s",
                session_id,
                run_id,
                tool_name,
            )


def _related_portfolio_ids(tool_name: str, result: object) -> tuple[str, ...]:
    if not isinstance(result, Mapping):
        return ()
    if tool_name in _PORTFOLIO_OBJECT_TOOLS:
        portfolio = result.get("portfolio")
        if not isinstance(portfolio, Mapping):
            return ()
        return _one_portfolio_id(portfolio.get("portfolio_id"))
    if tool_name in _PORTFOLIO_LEDGER_TOOLS:
        return _one_portfolio_id(result.get("portfolio_id"))
    if tool_name in _PORTFOLIO_OPERATION_TOOLS:
        return _entry_portfolio_ids(result.get("entries"))
    return ()


def _one_portfolio_id(value: object) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) and value else ()


def _entry_portfolio_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        return ()
    portfolio_ids: list[str] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            return ()
        portfolio_id = entry.get("portfolio_id")
        if not isinstance(portfolio_id, str) or not portfolio_id:
            return ()
        if portfolio_id not in seen:
            portfolio_ids.append(portfolio_id)
            seen.add(portfolio_id)
    return tuple(portfolio_ids)
