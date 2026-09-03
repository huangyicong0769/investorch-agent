from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from investorch.config import AppConfig
from investorch.portfolio import (
    LedgerEntry,
    LedgerEntryType,
    OpeningCash,
    OpeningPosition,
    Portfolio,
    PortfolioNotFoundError,
    PortfolioSequenceConflictError,
    PortfolioState,
    PortfolioStatus,
    StrategyBinding,
    append_ledger_operation,
    create_portfolio,
    get_portfolio,
    get_portfolio_state,
    list_ledger_entries,
    list_portfolios,
    update_portfolio_metadata,
)
from investorch.portfolio.domain import LedgerPayload

logger = logging.getLogger(__name__)


class PortfolioOperationError(RuntimeError):
    """Base error for Portfolio application use-case failures."""


class PortfolioArchivedError(PortfolioOperationError):
    """Raised when a frozen Portfolio receives a mutation."""


class PortfolioAlreadyArchivedError(PortfolioOperationError):
    """Raised when an archived Portfolio is archived again."""


class PortfolioAlreadyActiveError(PortfolioOperationError):
    """Raised when an active Portfolio is restored."""


class PortfolioAlreadyInitializedError(PortfolioOperationError):
    """Raised when opening state is requested after Ledger history exists."""


class PortfolioSequenceRetryExhaustedError(PortfolioOperationError):
    """Raised when Ledger append sequence conflicts exhaust their retry limit."""


@dataclass(frozen=True, slots=True)
class PortfolioMutationResult:
    operation_id: str
    entries: tuple[LedgerEntry, ...]
    states: dict[str, PortfolioState]


@dataclass(frozen=True, slots=True)
class _EntryDraft:
    entry_id: str
    portfolio_id: str
    entry_type: LedgerEntryType
    effective_at: datetime
    payload: LedgerPayload


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

    async def initialize(
        self,
        portfolio_id: str,
        *,
        cash: Decimal | None = None,
        positions: Iterable[OpeningPosition] = (),
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        positions = tuple(positions)
        if cash is None and not positions:
            raise PortfolioOperationError("Portfolio initialization requires opening state")
        recorded_at = datetime.now(UTC)
        effective_at = recorded_at if effective_at is None else effective_at
        operation_id = uuid.uuid4().hex

        async with self._mutation_lock:
            portfolio = await self.get(portfolio_id)
            _require_active(portfolio)
            payloads: list[tuple[LedgerEntryType, LedgerPayload]] = []
            if cash is not None:
                payloads.append((LedgerEntryType.OPENING_CASH, OpeningCash(portfolio.base_currency, cash)))
            payloads.extend((LedgerEntryType.OPENING_POSITION, position) for position in positions)
            drafts = tuple(
                _EntryDraft(
                    entry_id=uuid.uuid4().hex,
                    portfolio_id=portfolio_id,
                    entry_type=entry_type,
                    effective_at=effective_at,
                    payload=payload,
                )
                for entry_type, payload in payloads
            )

            def validate(
                portfolios: dict[str, Portfolio],
                ledgers: dict[str, list[LedgerEntry]],
            ) -> None:
                _require_active(portfolios[portfolio_id])
                if ledgers[portfolio_id]:
                    raise PortfolioAlreadyInitializedError(f"Portfolio is already initialized: {portfolio_id}")

            result = await self._append_operation(
                operation_id=operation_id,
                recorded_at=recorded_at,
                source=source,
                external_ref=external_ref,
                drafts=drafts,
                validate=validate,
            )
        logger.info("Initialized Portfolio operation %s for %s", operation_id, portfolio_id)
        return result

    async def _append_operation(
        self,
        *,
        operation_id: str,
        recorded_at: datetime,
        source: str,
        external_ref: str | None,
        drafts: tuple[_EntryDraft, ...],
        validate: Callable[[dict[str, Portfolio], dict[str, list[LedgerEntry]]], None] | None = None,
    ) -> PortfolioMutationResult:
        portfolio_ids = tuple(dict.fromkeys(draft.portfolio_id for draft in drafts))
        for attempt in range(3):
            portfolios = {portfolio_id: await self.get(portfolio_id) for portfolio_id in portfolio_ids}
            ledgers = {
                portfolio_id: await asyncio.to_thread(
                    list_ledger_entries,
                    self._config.portfolio_db,
                    portfolio_id,
                )
                for portfolio_id in portfolio_ids
            }
            if validate is not None:
                validate(portfolios, ledgers)
            entries = _assign_sequences(
                operation_id=operation_id,
                recorded_at=recorded_at,
                source=source,
                external_ref=external_ref,
                drafts=drafts,
                ledgers=ledgers,
            )
            try:
                states = await asyncio.to_thread(
                    append_ledger_operation,
                    self._config.portfolio_db,
                    entries,
                )
            except PortfolioSequenceConflictError as exc:
                if attempt == 2:
                    raise PortfolioSequenceRetryExhaustedError(
                        f"Portfolio Ledger sequence retry exhausted for operation {operation_id}"
                    ) from exc
                continue
            return PortfolioMutationResult(operation_id, entries, states)
        raise AssertionError("unreachable")


def _require_active(portfolio: Portfolio) -> None:
    if portfolio.status is PortfolioStatus.ARCHIVED:
        raise PortfolioArchivedError(f"Portfolio is archived: {portfolio.id}")


def _assign_sequences(
    *,
    operation_id: str,
    recorded_at: datetime,
    source: str,
    external_ref: str | None,
    drafts: tuple[_EntryDraft, ...],
    ledgers: dict[str, list[LedgerEntry]],
) -> tuple[LedgerEntry, ...]:
    next_sequences = {
        portfolio_id: max((entry.sequence for entry in entries), default=0) + 1
        for portfolio_id, entries in ledgers.items()
    }
    assigned: list[LedgerEntry] = []
    for draft in drafts:
        sequence = next_sequences[draft.portfolio_id]
        assigned.append(
            LedgerEntry(
                entry_id=draft.entry_id,
                operation_id=operation_id,
                portfolio_id=draft.portfolio_id,
                sequence=sequence,
                entry_type=draft.entry_type,
                effective_at=draft.effective_at,
                recorded_at=recorded_at,
                source=source,
                external_ref=external_ref,
                payload=draft.payload,
            )
        )
        next_sequences[draft.portfolio_id] += 1
    return tuple(assigned)
