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
    PortfolioNotFoundError,
    PortfolioSequenceConflictError,
    PortfolioState,
    PortfolioStatus,
    PositionAdjustment,
    PositionTransfer,
    StrategyBinding,
    Trade,
    TradeSide,
    TransferDirection,
    Void,
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


class PortfolioCorrectionError(PortfolioOperationError):
    """Raised when a requested Ledger correction is not valid."""


class PortfolioTransferCurrencyError(PortfolioOperationError):
    """Raised when an internal transfer crosses base currencies."""


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

    async def record_trade(
        self,
        portfolio_id: str,
        *,
        instrument: InstrumentId,
        side: TradeSide,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal = Decimal(0),
        tax: Decimal = Decimal(0),
        other_fee: Decimal = Decimal(0),
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        return await self._record_single(
            portfolio_id=portfolio_id,
            entry_type=LedgerEntryType.TRADE,
            payload_factory=lambda _portfolio: Trade(
                instrument,
                side,
                quantity,
                price,
                commission,
                tax,
                other_fee,
            ),
            effective_at=effective_at,
            source=source,
            external_ref=external_ref,
        )

    async def record_cash_flow(
        self,
        portfolio_id: str,
        *,
        amount: Decimal,
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        return await self._record_single(
            portfolio_id=portfolio_id,
            entry_type=LedgerEntryType.CASH_FLOW,
            payload_factory=lambda portfolio: CashFlow(portfolio.base_currency, amount),
            effective_at=effective_at,
            source=source,
            external_ref=external_ref,
        )

    async def record_income(
        self,
        portfolio_id: str,
        *,
        gross_amount: Decimal,
        tax: Decimal = Decimal(0),
        other_fee: Decimal = Decimal(0),
        instrument: InstrumentId | None = None,
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        return await self._record_single(
            portfolio_id=portfolio_id,
            entry_type=LedgerEntryType.INCOME,
            payload_factory=lambda portfolio: Income(
                portfolio.base_currency,
                gross_amount,
                tax,
                other_fee,
                instrument,
            ),
            effective_at=effective_at,
            source=source,
            external_ref=external_ref,
        )

    async def adjust_position(
        self,
        portfolio_id: str,
        *,
        instrument: InstrumentId,
        resulting_quantity: Decimal,
        resulting_total_cost: Decimal | None,
        reason: str,
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        return await self._record_single(
            portfolio_id=portfolio_id,
            entry_type=LedgerEntryType.ADJUSTMENT,
            payload_factory=lambda _portfolio: PositionAdjustment(
                instrument,
                resulting_quantity,
                resulting_total_cost,
                reason,
            ),
            effective_at=effective_at,
            source=source,
            external_ref=external_ref,
        )

    async def adjust_cash(
        self,
        portfolio_id: str,
        *,
        resulting_amount: Decimal,
        reason: str,
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        return await self._record_single(
            portfolio_id=portfolio_id,
            entry_type=LedgerEntryType.ADJUSTMENT,
            payload_factory=lambda portfolio: CashAdjustment(
                portfolio.base_currency,
                resulting_amount,
                reason,
            ),
            effective_at=effective_at,
            source=source,
            external_ref=external_ref,
        )

    async def correct_entry(
        self,
        portfolio_id: str,
        *,
        target_entry_id: str,
        replacement_payload: LedgerPayload,
        effective_at: datetime | None = None,
        reason: str,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        recorded_at = datetime.now(UTC)
        operation_id = uuid.uuid4().hex
        async with self._mutation_lock:
            portfolio = await self.get(portfolio_id)
            _require_active(portfolio)
            ledger = await asyncio.to_thread(list_ledger_entries, self._config.portfolio_db, portfolio_id)
            target = _correction_target(ledger, target_entry_id)
            if isinstance(replacement_payload, Void):
                raise PortfolioCorrectionError("correction replacement cannot be VOID")
            replacement_type = _entry_type_for_payload(replacement_payload)
            replacement_effective_at = target.effective_at if effective_at is None else effective_at
            drafts = (
                _EntryDraft(
                    entry_id=uuid.uuid4().hex,
                    portfolio_id=portfolio_id,
                    entry_type=LedgerEntryType.VOID,
                    effective_at=target.effective_at,
                    payload=Void(target_entry_id, reason),
                ),
                _EntryDraft(
                    entry_id=uuid.uuid4().hex,
                    portfolio_id=portfolio_id,
                    entry_type=replacement_type,
                    effective_at=replacement_effective_at,
                    payload=replacement_payload,
                ),
            )

            def validate(
                portfolios: dict[str, Portfolio],
                ledgers: dict[str, list[LedgerEntry]],
            ) -> None:
                _require_active(portfolios[portfolio_id])
                _correction_target(ledgers[portfolio_id], target_entry_id)

            result = await self._append_operation(
                operation_id=operation_id,
                recorded_at=recorded_at,
                source=source,
                external_ref=external_ref,
                drafts=drafts,
                validate=validate,
            )
        logger.info("Corrected Portfolio entry operation %s for %s", operation_id, portfolio_id)
        return result

    async def transfer_position(
        self,
        *,
        source_portfolio_id: str,
        destination_portfolio_id: str,
        instrument: InstrumentId,
        quantity: Decimal,
        transferred_cost: Decimal | None,
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        return await self._record_transfer(
            source_portfolio_id=source_portfolio_id,
            destination_portfolio_id=destination_portfolio_id,
            payload_factory=lambda _source_portfolio, _destination: (
                PositionTransfer(
                    instrument,
                    TransferDirection.OUT,
                    quantity,
                    transferred_cost,
                ),
                PositionTransfer(
                    instrument,
                    TransferDirection.IN,
                    quantity,
                    transferred_cost,
                ),
            ),
            effective_at=effective_at,
            source=source,
            external_ref=external_ref,
        )

    async def transfer_cash(
        self,
        *,
        source_portfolio_id: str,
        destination_portfolio_id: str,
        amount: Decimal,
        effective_at: datetime | None = None,
        source: str,
        external_ref: str | None = None,
    ) -> PortfolioMutationResult:
        return await self._record_transfer(
            source_portfolio_id=source_portfolio_id,
            destination_portfolio_id=destination_portfolio_id,
            payload_factory=lambda source_portfolio, _destination: (
                CashTransfer(source_portfolio.base_currency, TransferDirection.OUT, amount),
                CashTransfer(source_portfolio.base_currency, TransferDirection.IN, amount),
            ),
            effective_at=effective_at,
            source=source,
            external_ref=external_ref,
        )

    async def _record_transfer(
        self,
        *,
        source_portfolio_id: str,
        destination_portfolio_id: str,
        payload_factory: Callable[[Portfolio, Portfolio], tuple[LedgerPayload, LedgerPayload]],
        effective_at: datetime | None,
        source: str,
        external_ref: str | None,
    ) -> PortfolioMutationResult:
        if source_portfolio_id == destination_portfolio_id:
            raise PortfolioOperationError("Portfolio transfer requires distinct source and destination")
        recorded_at = datetime.now(UTC)
        effective_at = recorded_at if effective_at is None else effective_at
        operation_id = uuid.uuid4().hex
        async with self._mutation_lock:
            source_portfolio = await self.get(source_portfolio_id)
            destination_portfolio = await self.get(destination_portfolio_id)
            _validate_transfer(source_portfolio, destination_portfolio)
            outgoing, incoming = payload_factory(source_portfolio, destination_portfolio)
            drafts = (
                _EntryDraft(
                    entry_id=uuid.uuid4().hex,
                    portfolio_id=source_portfolio_id,
                    entry_type=LedgerEntryType.TRANSFER,
                    effective_at=effective_at,
                    payload=outgoing,
                ),
                _EntryDraft(
                    entry_id=uuid.uuid4().hex,
                    portfolio_id=destination_portfolio_id,
                    entry_type=LedgerEntryType.TRANSFER,
                    effective_at=effective_at,
                    payload=incoming,
                ),
            )

            def validate(
                portfolios: dict[str, Portfolio],
                _ledgers: dict[str, list[LedgerEntry]],
            ) -> None:
                _validate_transfer(
                    portfolios[source_portfolio_id],
                    portfolios[destination_portfolio_id],
                )

            result = await self._append_operation(
                operation_id=operation_id,
                recorded_at=recorded_at,
                source=source,
                external_ref=external_ref,
                drafts=drafts,
                validate=validate,
            )
        logger.info(
            "Transferred Portfolio operation=%s source=%s destination=%s",
            operation_id,
            source_portfolio_id,
            destination_portfolio_id,
        )
        return result

    async def _record_single(
        self,
        *,
        portfolio_id: str,
        entry_type: LedgerEntryType,
        payload_factory: Callable[[Portfolio], LedgerPayload],
        effective_at: datetime | None,
        source: str,
        external_ref: str | None,
    ) -> PortfolioMutationResult:
        recorded_at = datetime.now(UTC)
        effective_at = recorded_at if effective_at is None else effective_at
        operation_id = uuid.uuid4().hex
        async with self._mutation_lock:
            portfolio = await self.get(portfolio_id)
            _require_active(portfolio)
            drafts = (
                _EntryDraft(
                    entry_id=uuid.uuid4().hex,
                    portfolio_id=portfolio_id,
                    entry_type=entry_type,
                    effective_at=effective_at,
                    payload=payload_factory(portfolio),
                ),
            )

            def validate(
                portfolios: dict[str, Portfolio],
                _ledgers: dict[str, list[LedgerEntry]],
            ) -> None:
                _require_active(portfolios[portfolio_id])

            result = await self._append_operation(
                operation_id=operation_id,
                recorded_at=recorded_at,
                source=source,
                external_ref=external_ref,
                drafts=drafts,
                validate=validate,
            )
        logger.info("Recorded Portfolio operation %s for %s", operation_id, portfolio_id)
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


def _validate_transfer(source: Portfolio, destination: Portfolio) -> None:
    _require_active(source)
    _require_active(destination)
    if source.base_currency != destination.base_currency:
        raise PortfolioTransferCurrencyError(
            f"Portfolio transfer requires one base currency: {source.base_currency} != {destination.base_currency}"
        )


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


_PAYLOAD_ENTRY_TYPES: dict[type, LedgerEntryType] = {
    OpeningPosition: LedgerEntryType.OPENING_POSITION,
    OpeningCash: LedgerEntryType.OPENING_CASH,
    Trade: LedgerEntryType.TRADE,
    CashFlow: LedgerEntryType.CASH_FLOW,
    Income: LedgerEntryType.INCOME,
    PositionTransfer: LedgerEntryType.TRANSFER,
    CashTransfer: LedgerEntryType.TRANSFER,
    PositionAdjustment: LedgerEntryType.ADJUSTMENT,
    CashAdjustment: LedgerEntryType.ADJUSTMENT,
}


def _entry_type_for_payload(payload: LedgerPayload) -> LedgerEntryType:
    try:
        return _PAYLOAD_ENTRY_TYPES[type(payload)]
    except KeyError as exc:
        raise PortfolioCorrectionError("correction replacement must be an ordinary Ledger payload") from exc


def _correction_target(ledger: list[LedgerEntry], target_entry_id: str) -> LedgerEntry:
    target = next((entry for entry in ledger if entry.entry_id == target_entry_id), None)
    if target is None:
        raise PortfolioCorrectionError(f"correction target not found: {target_entry_id}")
    if target.entry_type is LedgerEntryType.VOID:
        raise PortfolioCorrectionError(f"correction cannot use a VOID target: {target_entry_id}")
    return target
