from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from investorch.config import AppConfig
from investorch.portfolio import (
    LedgerEntry,
    Portfolio,
    PortfolioNotFoundError,
    PortfolioState,
    PortfolioStatus,
    StrategyBinding,
    create_portfolio,
    get_portfolio,
    get_portfolio_state,
    list_ledger_entries,
    list_portfolios,
    update_portfolio_metadata,
)

logger = logging.getLogger(__name__)


class PortfolioOperationError(RuntimeError):
    """Base error for Portfolio application use-case failures."""


class PortfolioArchivedError(PortfolioOperationError):
    """Raised when a frozen Portfolio receives a mutation."""


class PortfolioAlreadyArchivedError(PortfolioOperationError):
    """Raised when an archived Portfolio is archived again."""


class PortfolioAlreadyActiveError(PortfolioOperationError):
    """Raised when an active Portfolio is restored."""


class _Unset:
    pass


_UNSET = _Unset()


class PortfolioOperations:
    """Execute Portfolio application use cases through A1 persistence."""

    def __init__(self, *, config: AppConfig) -> None:
        self._config = config
        self._mutation_lock = asyncio.Lock()

    async def get(self, portfolio_id: str) -> Portfolio:
        portfolio = await asyncio.to_thread(get_portfolio, self._config.portfolio_db, portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"Portfolio not found: {portfolio_id}")
        return portfolio

    async def list(self, *, include_archived: bool = False) -> list[Portfolio]:
        return await asyncio.to_thread(
            list_portfolios,
            self._config.portfolio_db,
            include_archived=include_archived,
        )

    async def get_state(self, portfolio_id: str) -> PortfolioState:
        return await asyncio.to_thread(get_portfolio_state, self._config.portfolio_db, portfolio_id)

    async def list_ledger(self, portfolio_id: str) -> list[LedgerEntry]:
        await self.get(portfolio_id)
        return await asyncio.to_thread(list_ledger_entries, self._config.portfolio_db, portfolio_id)

    async def create(
        self,
        *,
        name: str,
        base_currency: str,
        description: str | None = None,
        strategy_binding: StrategyBinding | None = None,
    ) -> Portfolio:
        async with self._mutation_lock:
            now = datetime.now(UTC)
            portfolio = Portfolio(
                id=uuid.uuid4().hex,
                name=name,
                base_currency=base_currency,
                created_at=now,
                updated_at=now,
                description=description,
                status=PortfolioStatus.ACTIVE,
                strategy_binding=strategy_binding,
            )
            await asyncio.to_thread(create_portfolio, self._config.portfolio_db, portfolio)
        logger.info("Created Portfolio %s", portfolio.id)
        return portfolio

    async def update_metadata(
        self,
        portfolio_id: str,
        *,
        name: str | _Unset = _UNSET,
        description: str | _Unset | None = _UNSET,
        strategy_binding: StrategyBinding | _Unset | None = _UNSET,
    ) -> Portfolio:
        async with self._mutation_lock:
            portfolio = await self.get(portfolio_id)
            _require_active(portfolio)
            if isinstance(name, _Unset) and isinstance(description, _Unset) and isinstance(strategy_binding, _Unset):
                return portfolio
            updated = replace(
                portfolio,
                name=portfolio.name if isinstance(name, _Unset) else name,
                description=portfolio.description if isinstance(description, _Unset) else description,
                strategy_binding=(
                    portfolio.strategy_binding if isinstance(strategy_binding, _Unset) else strategy_binding
                ),
                updated_at=datetime.now(UTC),
            )
            await asyncio.to_thread(update_portfolio_metadata, self._config.portfolio_db, updated)
        logger.info("Updated Portfolio %s", portfolio_id)
        return updated

    async def archive(self, portfolio_id: str) -> Portfolio:
        async with self._mutation_lock:
            portfolio = await self.get(portfolio_id)
            if portfolio.status is PortfolioStatus.ARCHIVED:
                raise PortfolioAlreadyArchivedError(f"Portfolio is already archived: {portfolio_id}")
            archived = replace(portfolio, status=PortfolioStatus.ARCHIVED, updated_at=datetime.now(UTC))
            await asyncio.to_thread(update_portfolio_metadata, self._config.portfolio_db, archived)
        logger.info("Archived Portfolio %s", portfolio_id)
        return archived

    async def restore(self, portfolio_id: str) -> Portfolio:
        async with self._mutation_lock:
            portfolio = await self.get(portfolio_id)
            if portfolio.status is PortfolioStatus.ACTIVE:
                raise PortfolioAlreadyActiveError(f"Portfolio is already active: {portfolio_id}")
            restored = replace(portfolio, status=PortfolioStatus.ACTIVE, updated_at=datetime.now(UTC))
            await asyncio.to_thread(update_portfolio_metadata, self._config.portfolio_db, restored)
        logger.info("Restored Portfolio %s", portfolio_id)
        return restored


def _require_active(portfolio: Portfolio) -> None:
    if portfolio.status is PortfolioStatus.ARCHIVED:
        raise PortfolioArchivedError(f"Portfolio is archived: {portfolio.id}")
