from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investorch.portfolio import (
    CashAdjustment,
    CashFlow,
    CashTransfer,
    Income,
    InstrumentId,
    InsufficientPositionError,
    LedgerEntry,
    LedgerEntryType,
    OpeningCash,
    OpeningPosition,
    Portfolio,
    PortfolioAlreadyExistsError,
    PortfolioConflictError,
    PortfolioDataError,
    PortfolioNotFoundError,
    PortfolioSequenceConflictError,
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
    init_portfolio_storage,
    list_ledger_entries,
    list_portfolios,
    project_portfolio,
    rebuild_portfolio_projection,
    update_portfolio_metadata,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
STOCK = InstrumentId("600519", "XSHG")
UNKNOWN_STOCK = InstrumentId("000001", "XSHE")


def make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "portfolio.db"
    init_portfolio_storage(db_path)
    return db_path


def make_entry(
    portfolio_id: str,
    sequence: int,
    entry_type: LedgerEntryType,
    payload,
    *,
    operation_id: str = "operation-1",
    effective_at: datetime | None = None,
    external_ref: str | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        entry_id=f"{portfolio_id}-entry-{sequence}",
        operation_id=operation_id,
        portfolio_id=portfolio_id,
        sequence=sequence,
        entry_type=entry_type,
        effective_at=effective_at or NOW + timedelta(days=sequence),
        recorded_at=NOW + timedelta(days=sequence),
        source="test",
        external_ref=external_ref,
        payload=payload,
    )


def test_portfolio_metadata_round_trips_through_sqlite(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio(
        "portfolio-1",
        "Core",
        "CNY",
        NOW,
        NOW + timedelta(hours=1),
        description="Long-term holdings",
        strategy_binding=StrategyBinding(
            "strategies/value.py",
            {"lookback": 20, "filters": {"markets": ["XSHG", "XSHE"], "enabled": True}},
        ),
    )

    create_portfolio(db_path, portfolio)

    assert get_portfolio(db_path, portfolio.id) == portfolio


def test_portfolio_listing_excludes_archived_by_default_and_is_deterministic(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    older = Portfolio("older", "Older", "CNY", NOW, NOW)
    newer = Portfolio("newer", "Newer", "USD", NOW, NOW + timedelta(days=1), description=None)
    archived = Portfolio("archived", "Archived", "CNY", NOW, NOW + timedelta(days=2), status=PortfolioStatus.ARCHIVED)
    for portfolio in (older, archived, newer):
        create_portfolio(db_path, portfolio)

    assert list_portfolios(db_path) == [newer, older]
    assert list_portfolios(db_path, include_archived=True) == [archived, newer, older]


def test_portfolio_metadata_update_changes_only_mutable_fields(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    original = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, original)
    updated = replace(
        original,
        name="Renamed",
        description="Updated",
        status=PortfolioStatus.ARCHIVED,
        strategy_binding=StrategyBinding("strategies/new.py", {"threshold": 0.1}),
        updated_at=NOW + timedelta(days=1),
    )

    update_portfolio_metadata(db_path, updated)

    assert get_portfolio(db_path, original.id) == updated

    with pytest.raises(PortfolioConflictError, match="base_currency"):
        update_portfolio_metadata(db_path, replace(updated, base_currency="USD"))
    with pytest.raises(PortfolioConflictError, match="created_at"):
        update_portfolio_metadata(db_path, replace(updated, created_at=NOW + timedelta(seconds=1)))


def test_portfolio_create_and_update_fail_with_typed_identity_errors(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)

    with pytest.raises(PortfolioAlreadyExistsError):
        create_portfolio(db_path, portfolio)
    with pytest.raises(PortfolioNotFoundError):
        update_portfolio_metadata(db_path, replace(portfolio, id="missing"))

    assert get_portfolio(db_path, "missing") is None


def test_all_ledger_payloads_round_trip_and_materialize_exact_state(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    entries = [
        make_entry(
            portfolio.id,
            1,
            LedgerEntryType.OPENING_POSITION,
            OpeningPosition(STOCK, Decimal("10"), Decimal("100")),
            external_ref="shared-ref",
        ),
        make_entry(
            portfolio.id,
            2,
            LedgerEntryType.OPENING_POSITION,
            OpeningPosition(UNKNOWN_STOCK, Decimal("0.2"), None),
        ),
        make_entry(portfolio.id, 3, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("100.1"))),
        make_entry(
            portfolio.id,
            4,
            LedgerEntryType.TRADE,
            Trade(STOCK, TradeSide.BUY, Decimal("1"), Decimal("10.2"), commission=Decimal("0.03")),
            external_ref="shared-ref",
        ),
        make_entry(
            portfolio.id,
            5,
            LedgerEntryType.TRADE,
            Trade(STOCK, TradeSide.SELL, Decimal("1"), Decimal("11"), tax=Decimal("0.1")),
        ),
        make_entry(portfolio.id, 6, LedgerEntryType.CASH_FLOW, CashFlow("CNY", Decimal("-0.2"))),
        make_entry(
            portfolio.id,
            7,
            LedgerEntryType.INCOME,
            Income("CNY", Decimal("1"), tax=Decimal("0.1"), other_fee=Decimal("0.03"), instrument=STOCK),
        ),
        make_entry(portfolio.id, 8, LedgerEntryType.INCOME, Income("CNY", Decimal("0.2"))),
        make_entry(
            portfolio.id,
            9,
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.OUT, Decimal("1"), Decimal("10")),
        ),
        make_entry(
            portfolio.id,
            10,
            LedgerEntryType.TRANSFER,
            CashTransfer("CNY", TransferDirection.IN, Decimal("0.1")),
        ),
        make_entry(
            portfolio.id,
            11,
            LedgerEntryType.ADJUSTMENT,
            PositionAdjustment(STOCK, Decimal("8"), Decimal("80"), "statement"),
        ),
        make_entry(
            portfolio.id,
            12,
            LedgerEntryType.ADJUSTMENT,
            CashAdjustment("CNY", Decimal("50.03"), "statement"),
        ),
        make_entry(portfolio.id, 13, LedgerEntryType.VOID, Void(f"{portfolio.id}-entry-6", "wrong cash flow")),
    ]

    states = append_ledger_operation(db_path, entries)

    assert list_ledger_entries(db_path, portfolio.id) == entries
    assert states == {portfolio.id: project_portfolio(portfolio, entries)}
    assert get_portfolio_state(db_path, portfolio.id) == states[portfolio.id]
    assert states[portfolio.id].holdings[UNKNOWN_STOCK].total_cost is None
    with sqlite3.connect(db_path) as connection:
        stored_unknown_cost = connection.execute(
            """
            SELECT total_cost FROM portfolio_holdings
            WHERE portfolio_id = ? AND instrument_code = ? AND market = ?
            """,
            (portfolio.id, UNKNOWN_STOCK.code, UNKNOWN_STOCK.market),
        ).fetchone()[0]
    assert stored_unknown_cost is None


def test_backdated_append_rebuilds_projection_by_economic_time(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    opening = make_entry(
        portfolio.id,
        1,
        LedgerEntryType.OPENING_POSITION,
        OpeningPosition(STOCK, Decimal("10"), Decimal("100")),
        operation_id="initial",
        effective_at=NOW,
    )
    sale = make_entry(
        portfolio.id,
        2,
        LedgerEntryType.TRADE,
        Trade(STOCK, TradeSide.SELL, Decimal("5"), Decimal("20")),
        operation_id="initial",
        effective_at=NOW + timedelta(days=2),
    )
    backdated_buy = make_entry(
        portfolio.id,
        3,
        LedgerEntryType.TRADE,
        Trade(STOCK, TradeSide.BUY, Decimal("5"), Decimal("20")),
        operation_id="backdated",
        effective_at=NOW + timedelta(days=1),
    )

    append_ledger_operation(db_path, [opening, sale])
    append_ledger_operation(db_path, [backdated_buy])

    persisted = list_ledger_entries(db_path, portfolio.id)
    assert get_portfolio_state(db_path, portfolio.id) == project_portfolio(portfolio, persisted)
    assert [entry.sequence for entry in persisted] == [1, 2, 3]


def test_void_and_replacement_preserve_history_but_replace_economic_effect(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    original = make_entry(
        portfolio.id,
        1,
        LedgerEntryType.OPENING_CASH,
        OpeningCash("CNY", Decimal("100")),
        operation_id="opening",
    )
    correction = [
        make_entry(
            portfolio.id,
            2,
            LedgerEntryType.VOID,
            Void(original.entry_id, "wrong amount"),
            operation_id="correction",
        ),
        make_entry(
            portfolio.id,
            3,
            LedgerEntryType.OPENING_CASH,
            OpeningCash("CNY", Decimal("120")),
            operation_id="correction",
        ),
    ]

    append_ledger_operation(db_path, [original])
    append_ledger_operation(db_path, correction)

    persisted = list_ledger_entries(db_path, portfolio.id)
    assert persisted == [original, *correction]
    assert get_portfolio_state(db_path, portfolio.id) == project_portfolio(portfolio, persisted)
    assert get_portfolio_state(db_path, portfolio.id).cash == {"CNY": Decimal("120")}


def test_multi_portfolio_ledger_operation_commits_or_rolls_back_as_one_unit(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    source = Portfolio("source", "Source", "CNY", NOW, NOW)
    destination = Portfolio("destination", "Destination", "CNY", NOW, NOW)
    create_portfolio(db_path, source)
    create_portfolio(db_path, destination)
    opening = make_entry(
        source.id,
        1,
        LedgerEntryType.OPENING_POSITION,
        OpeningPosition(STOCK, Decimal("10"), Decimal("100")),
        operation_id="opening",
    )
    append_ledger_operation(db_path, [opening])
    transfer = [
        make_entry(
            source.id,
            2,
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.OUT, Decimal("4"), Decimal("40")),
            operation_id="transfer",
        ),
        make_entry(
            destination.id,
            1,
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.IN, Decimal("4"), Decimal("40")),
            operation_id="transfer",
        ),
    ]

    committed_states = append_ledger_operation(db_path, transfer)

    assert committed_states[source.id].holdings[STOCK].quantity == Decimal("6")
    assert committed_states[destination.id].holdings[STOCK].quantity == Decimal("4")
    ledger_before_failure = {
        source.id: list_ledger_entries(db_path, source.id),
        destination.id: list_ledger_entries(db_path, destination.id),
    }
    states_before_failure = {
        source.id: get_portfolio_state(db_path, source.id),
        destination.id: get_portfolio_state(db_path, destination.id),
    }
    invalid_transfer = [
        make_entry(
            source.id,
            3,
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.OUT, Decimal("7"), Decimal("70")),
            operation_id="invalid-transfer",
        ),
        make_entry(
            destination.id,
            2,
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.IN, Decimal("7"), Decimal("70")),
            operation_id="invalid-transfer",
        ),
    ]

    with pytest.raises(InsufficientPositionError, match="insufficient position"):
        append_ledger_operation(db_path, invalid_transfer)

    assert list_ledger_entries(db_path, source.id) == ledger_before_failure[source.id]
    assert list_ledger_entries(db_path, destination.id) == ledger_before_failure[destination.id]
    assert get_portfolio_state(db_path, source.id) == states_before_failure[source.id]
    assert get_portfolio_state(db_path, destination.id) == states_before_failure[destination.id]


def test_projection_can_be_rebuilt_from_authoritative_ledger(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    entries = [
        make_entry(
            portfolio.id,
            1,
            LedgerEntryType.OPENING_POSITION,
            OpeningPosition(STOCK, Decimal("10"), Decimal("100")),
        ),
        make_entry(portfolio.id, 2, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("50"))),
    ]
    expected = append_ledger_operation(db_path, entries)[portfolio.id]
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE portfolio_holdings SET quantity = '999' WHERE portfolio_id = ?",
            (portfolio.id,),
        )
        connection.execute("DELETE FROM portfolio_cash WHERE portfolio_id = ?", (portfolio.id,))

    rebuilt = rebuild_portfolio_projection(db_path, portfolio.id)

    assert rebuilt == expected
    assert get_portfolio_state(db_path, portfolio.id) == expected


def test_projection_rebuild_rejects_missing_portfolio(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)

    with pytest.raises(PortfolioNotFoundError, match="missing"):
        rebuild_portfolio_projection(db_path, "missing")


def test_ledger_sequence_cannot_move_behind_persisted_audit_order(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    opening = make_entry(
        portfolio.id,
        10,
        LedgerEntryType.OPENING_CASH,
        OpeningCash("CNY", Decimal("10")),
        operation_id="opening",
    )
    append_ledger_operation(db_path, [opening])
    state_before = get_portfolio_state(db_path, portfolio.id)

    with pytest.raises(PortfolioSequenceConflictError, match="sequence"):
        append_ledger_operation(
            db_path,
            [
                make_entry(
                    portfolio.id,
                    5,
                    LedgerEntryType.CASH_FLOW,
                    CashFlow("CNY", Decimal("5")),
                    operation_id="late-write",
                )
            ],
        )

    assert list_ledger_entries(db_path, portfolio.id) == [opening]
    assert get_portfolio_state(db_path, portfolio.id) == state_before


@pytest.mark.parametrize(
    ("new_sequences", "expected_cash"),
    [
        ((11,), Decimal("11")),
        ((15, 11), Decimal("12")),
    ],
)
def test_ledger_sequence_can_advance_without_being_contiguous_or_input_sorted(
    tmp_path: Path,
    new_sequences: tuple[int, ...],
    expected_cash: Decimal,
) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    opening = make_entry(
        portfolio.id,
        10,
        LedgerEntryType.OPENING_CASH,
        OpeningCash("CNY", Decimal("10")),
        operation_id="opening",
    )
    append_ledger_operation(db_path, [opening])

    append_ledger_operation(
        db_path,
        [
            make_entry(
                portfolio.id,
                sequence,
                LedgerEntryType.CASH_FLOW,
                CashFlow("CNY", Decimal("1")),
                operation_id="next-operation",
            )
            for sequence in new_sequences
        ],
    )

    assert [entry.sequence for entry in list_ledger_entries(db_path, portfolio.id)] == [10, *sorted(new_sequences)]
    assert get_portfolio_state(db_path, portfolio.id).cash == {"CNY": expected_cash}


def test_invalid_sequence_rolls_back_entire_multi_portfolio_operation(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    first = Portfolio("portfolio-a", "First", "CNY", NOW, NOW)
    second = Portfolio("portfolio-b", "Second", "CNY", NOW, NOW)
    create_portfolio(db_path, first)
    create_portfolio(db_path, second)
    openings = [
        make_entry(
            portfolio.id,
            10,
            LedgerEntryType.OPENING_CASH,
            OpeningCash("CNY", Decimal("10")),
            operation_id="opening",
        )
        for portfolio in (first, second)
    ]
    append_ledger_operation(db_path, openings)
    states_before = {portfolio.id: get_portfolio_state(db_path, portfolio.id) for portfolio in (first, second)}
    proposed = [
        make_entry(
            first.id,
            11,
            LedgerEntryType.CASH_FLOW,
            CashFlow("CNY", Decimal("1")),
            operation_id="multi-write",
        ),
        make_entry(
            second.id,
            5,
            LedgerEntryType.CASH_FLOW,
            CashFlow("CNY", Decimal("1")),
            operation_id="multi-write",
        ),
    ]

    with pytest.raises(PortfolioConflictError, match="sequence"):
        append_ledger_operation(db_path, proposed)

    assert list_ledger_entries(db_path, first.id) == [openings[0]]
    assert list_ledger_entries(db_path, second.id) == [openings[1]]
    assert get_portfolio_state(db_path, first.id) == states_before[first.id]
    assert get_portfolio_state(db_path, second.id) == states_before[second.id]


def test_ledger_operation_rejects_invalid_grouping_and_missing_portfolios(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    first = make_entry(portfolio.id, 1, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("10")))
    second = make_entry(
        portfolio.id,
        2,
        LedgerEntryType.CASH_FLOW,
        CashFlow("CNY", Decimal("1")),
        operation_id="operation-2",
    )

    with pytest.raises(PortfolioConflictError, match="at least one"):
        append_ledger_operation(db_path, [])
    with pytest.raises(PortfolioConflictError, match="LedgerEntry"):
        append_ledger_operation(db_path, [object()])  # type: ignore[list-item]
    with pytest.raises(PortfolioConflictError, match="operation_id"):
        append_ledger_operation(db_path, [first, second])
    with pytest.raises(PortfolioNotFoundError, match="missing"):
        append_ledger_operation(
            db_path,
            [make_entry("missing", 1, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("10")))],
        )

    assert list_ledger_entries(db_path, portfolio.id) == []


@pytest.mark.parametrize("conflict", ["entry_id", "sequence"])
def test_duplicate_ledger_identity_rolls_back_without_projection_drift(tmp_path: Path, conflict: str) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    opening = make_entry(
        portfolio.id,
        1,
        LedgerEntryType.OPENING_CASH,
        OpeningCash("CNY", Decimal("10")),
        operation_id="opening",
    )
    append_ledger_operation(db_path, [opening])
    duplicate = make_entry(
        portfolio.id,
        2,
        LedgerEntryType.CASH_FLOW,
        CashFlow("CNY", Decimal("5")),
        operation_id="conflict",
    )
    if conflict == "entry_id":
        duplicate = replace(duplicate, entry_id=opening.entry_id)
    else:
        duplicate = replace(duplicate, sequence=opening.sequence)
    valid = make_entry(
        portfolio.id,
        3,
        LedgerEntryType.CASH_FLOW,
        CashFlow("CNY", Decimal("1")),
        operation_id="conflict",
    )
    state_before = get_portfolio_state(db_path, portfolio.id)

    with pytest.raises(PortfolioConflictError, match=conflict):
        append_ledger_operation(db_path, [valid, duplicate])

    assert list_ledger_entries(db_path, portfolio.id) == [opening]
    assert get_portfolio_state(db_path, portfolio.id) == state_before


def test_corrupt_persisted_payload_raises_contextual_typed_error(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO portfolio_ledger (
                entry_id, portfolio_id, operation_id, sequence, entry_type,
                effective_at, recorded_at, source, external_ref, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "corrupt-entry",
                portfolio.id,
                "corrupt-operation",
                1,
                LedgerEntryType.OPENING_CASH.value,
                NOW.isoformat(),
                NOW.isoformat(),
                "test",
                None,
                '{"amount":0.1,"currency":"CNY"}',
            ),
        )

    with pytest.raises(PortfolioDataError, match=r"portfolio-1.*corrupt-entry.*OPENING_CASH"):
        list_ledger_entries(db_path, portfolio.id)
