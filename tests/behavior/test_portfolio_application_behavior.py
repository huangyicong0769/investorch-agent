from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investorch.application import (
    PortfolioAlreadyActiveError,
    PortfolioAlreadyArchivedError,
    PortfolioAlreadyInitializedError,
    PortfolioArchivedError,
    PortfolioOperationError,
    PortfolioOperations,
)
from investorch.portfolio import (
    InstrumentId,
    LedgerEntryType,
    OpeningPosition,
    PortfolioNotFoundError,
    PortfolioStatus,
    StrategyBinding,
)
from tests.support.config import make_test_config

STOCK = InstrumentId("600519", "XSHG")
UNKNOWN_STOCK = InstrumentId("000001", "XSHE")
EFFECTIVE_AT = datetime(2025, 12, 31, tzinfo=UTC)


async def test_portfolio_application_creates_and_reads_durable_portfolios(tmp_path) -> None:
    config = make_test_config(tmp_path)
    operations = PortfolioOperations(config=config)
    binding = StrategyBinding("strategies/value.py", {"lookback": 20})

    created = await operations.create(
        name="Core",
        base_currency="CNY",
        description="Long-term holdings",
        strategy_binding=binding,
    )

    assert created.id
    assert created.status is PortfolioStatus.ACTIVE
    assert created.base_currency == "CNY"
    assert created.description == "Long-term holdings"
    assert created.strategy_binding == binding
    assert created.created_at == created.updated_at
    assert await operations.get(created.id) == created
    assert await operations.list() == [created]
    assert (await operations.get_state(created.id)).portfolio_id == created.id
    assert await operations.list_ledger(created.id) == []


async def test_portfolio_application_distinguishes_missing_portfolio_from_empty_ledger(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))

    with pytest.raises(PortfolioNotFoundError, match="missing"):
        await operations.get("missing")
    with pytest.raises(PortfolioNotFoundError, match="missing"):
        await operations.list_ledger("missing")


async def test_active_portfolio_metadata_can_be_changed_and_cleared(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))
    portfolio = await operations.create(
        name="Core",
        base_currency="CNY",
        description="Initial",
        strategy_binding=StrategyBinding("strategies/value.py", {"lookback": 20}),
    )

    updated = await operations.update_metadata(
        portfolio.id,
        name="Renamed",
        description=None,
        strategy_binding=None,
    )

    assert updated.name == "Renamed"
    assert updated.description is None
    assert updated.strategy_binding is None
    assert updated.updated_at >= portfolio.updated_at
    assert await operations.get(portfolio.id) == updated
    assert await operations.update_metadata(portfolio.id) == updated


async def test_archive_freezes_metadata_until_restore(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))
    portfolio = await operations.create(name="Core", base_currency="CNY")

    archived = await operations.archive(portfolio.id)

    assert archived.status is PortfolioStatus.ARCHIVED
    assert await operations.list() == []
    assert await operations.list(include_archived=True) == [archived]
    assert await operations.list_ledger(portfolio.id) == []
    with pytest.raises(PortfolioAlreadyArchivedError):
        await operations.archive(portfolio.id)
    with pytest.raises(PortfolioArchivedError):
        await operations.update_metadata(portfolio.id, name="Blocked")
    assert (await operations.get(portfolio.id)).name == "Core"

    restored = await operations.restore(portfolio.id)

    assert restored.status is PortfolioStatus.ACTIVE
    assert await operations.list() == [restored]
    with pytest.raises(PortfolioAlreadyActiveError):
        await operations.restore(portfolio.id)


async def test_initialize_persists_one_atomic_opening_state(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))
    portfolio = await operations.create(name="Core", base_currency="CNY")

    result = await operations.initialize(
        portfolio.id,
        cash=Decimal("1000"),
        positions=(
            OpeningPosition(STOCK, Decimal("10"), Decimal("100")),
            OpeningPosition(UNKNOWN_STOCK, Decimal("2"), None),
        ),
        effective_at=EFFECTIVE_AT,
        source="import",
        external_ref="opening-statement",
    )

    assert [entry.entry_type for entry in result.entries] == [
        LedgerEntryType.OPENING_CASH,
        LedgerEntryType.OPENING_POSITION,
        LedgerEntryType.OPENING_POSITION,
    ]
    assert [entry.sequence for entry in result.entries] == [1, 2, 3]
    assert {entry.operation_id for entry in result.entries} == {result.operation_id}
    assert {entry.recorded_at for entry in result.entries} == {result.entries[0].recorded_at}
    assert {entry.effective_at for entry in result.entries} == {EFFECTIVE_AT}
    assert {entry.source for entry in result.entries} == {"import"}
    assert {entry.external_ref for entry in result.entries} == {"opening-statement"}
    assert await operations.list_ledger(portfolio.id) == list(result.entries)
    assert await operations.get_state(portfolio.id) == result.states[portfolio.id]
    assert result.states[portfolio.id].cash == {"CNY": Decimal("1000")}
    assert result.states[portfolio.id].holdings[STOCK].total_cost == Decimal("100")
    assert result.states[portfolio.id].holdings[UNKNOWN_STOCK].total_cost is None


async def test_initialize_is_one_shot_and_rejects_empty_or_archived_portfolios(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))
    empty = await operations.create(name="Empty", base_currency="CNY")
    archived = await operations.create(name="Archived", base_currency="CNY")

    with pytest.raises(PortfolioOperationError):
        await operations.initialize(empty.id, source="manual")
    assert await operations.list_ledger(empty.id) == []

    await operations.initialize(empty.id, cash=Decimal("1"), source="manual")
    with pytest.raises(PortfolioAlreadyInitializedError):
        await operations.initialize(empty.id, cash=Decimal("2"), source="manual")
    assert len(await operations.list_ledger(empty.id)) == 1

    await operations.archive(archived.id)
    with pytest.raises(PortfolioArchivedError):
        await operations.initialize(archived.id, cash=Decimal("1"), source="manual")
    assert await operations.list_ledger(archived.id) == []
