from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investorch.portfolio import (
    CashAdjustment,
    CashFlow,
    CashTransfer,
    CurrencyMismatchError,
    Income,
    InstrumentId,
    InvalidVoidError,
    LedgerEntry,
    LedgerEntryType,
    OpeningCash,
    OpeningPosition,
    Portfolio,
    PositionAdjustment,
    PositionTransfer,
    Trade,
    TradeSide,
    TransferDirection,
    Void,
    project_portfolio,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
STOCK = InstrumentId("600519", "XSHG")


def make_portfolio(portfolio_id: str = "portfolio-1", currency: str = "CNY") -> Portfolio:
    return Portfolio(portfolio_id, "Core", currency, NOW, NOW)


def make_entry(
    portfolio_id: str,
    sequence: int,
    entry_type: LedgerEntryType,
    payload,
    *,
    effective_at: datetime | None = None,
    operation_id: str | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        entry_id=f"{portfolio_id}-entry-{sequence}",
        operation_id=operation_id or f"operation-{sequence}",
        portfolio_id=portfolio_id,
        sequence=sequence,
        entry_type=entry_type,
        effective_at=effective_at or NOW + timedelta(days=sequence),
        recorded_at=NOW + timedelta(days=sequence),
        source="test",
        payload=payload,
    )


def test_opening_position_and_logical_cash_are_projected() -> None:
    portfolio = make_portfolio()
    entries = [
        make_entry(
            portfolio.id, 1, LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("100"), Decimal("9000"))
        ),
        make_entry(portfolio.id, 2, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("1000"))),
    ]

    state = project_portfolio(portfolio, entries)

    assert state.holdings[STOCK].quantity == Decimal("100")
    assert state.holdings[STOCK].total_cost == Decimal("9000")
    assert state.holdings[STOCK].average_cost == Decimal("90")
    assert state.cash == {"CNY": Decimal("1000")}


def test_opening_position_may_have_unknown_cost() -> None:
    portfolio = make_portfolio()
    entry = make_entry(portfolio.id, 1, LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("100"), None))

    holding = project_portfolio(portfolio, [entry]).holdings[STOCK]

    assert holding.quantity == Decimal("100")
    assert holding.total_cost is None
    assert holding.average_cost is None


def test_multiple_buys_use_exact_moving_weighted_average_cost() -> None:
    portfolio = make_portfolio()
    entries = [
        make_entry(portfolio.id, 1, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("1000"))),
        make_entry(
            portfolio.id,
            2,
            LedgerEntryType.TRADE,
            Trade(STOCK, TradeSide.BUY, Decimal("3"), Decimal("10.10"), commission=Decimal("0.20")),
        ),
        make_entry(
            portfolio.id,
            3,
            LedgerEntryType.TRADE,
            Trade(STOCK, TradeSide.BUY, Decimal("2"), Decimal("20.20"), commission=Decimal("0.10")),
        ),
    ]

    state = project_portfolio(portfolio, entries)

    assert state.holdings[STOCK].quantity == Decimal("5")
    assert state.holdings[STOCK].total_cost == Decimal("71.00")
    assert state.holdings[STOCK].average_cost == Decimal("14.20")
    assert state.cash["CNY"] == Decimal("929.00")


def test_sell_removes_pre_sale_average_cost_and_adds_net_cash() -> None:
    portfolio = make_portfolio()
    entries = [
        make_entry(
            portfolio.id, 1, LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("10"), Decimal("100"))
        ),
        make_entry(portfolio.id, 2, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("0"))),
        make_entry(
            portfolio.id,
            3,
            LedgerEntryType.TRADE,
            Trade(
                STOCK,
                TradeSide.SELL,
                Decimal("4"),
                Decimal("20"),
                commission=Decimal("1"),
                tax=Decimal("2"),
                other_fee=Decimal("0.5"),
            ),
        ),
    ]

    state = project_portfolio(portfolio, entries)

    assert state.holdings[STOCK].quantity == Decimal("6")
    assert state.holdings[STOCK].total_cost == Decimal("60")
    assert state.holdings[STOCK].average_cost == Decimal("10")
    assert state.cash["CNY"] == Decimal("76.5")


def test_unknown_cost_propagates_until_full_exit_then_fresh_buy() -> None:
    portfolio = make_portfolio()
    entries = [
        make_entry(portfolio.id, 1, LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("10"), None)),
        make_entry(portfolio.id, 2, LedgerEntryType.TRADE, Trade(STOCK, TradeSide.BUY, Decimal("5"), Decimal("2"))),
        make_entry(portfolio.id, 3, LedgerEntryType.TRADE, Trade(STOCK, TradeSide.SELL, Decimal("4"), Decimal("3"))),
        make_entry(portfolio.id, 4, LedgerEntryType.TRADE, Trade(STOCK, TradeSide.SELL, Decimal("11"), Decimal("3"))),
        make_entry(portfolio.id, 5, LedgerEntryType.TRADE, Trade(STOCK, TradeSide.BUY, Decimal("2"), Decimal("7"))),
    ]

    after_purchase = project_portfolio(portfolio, entries[:2]).holdings[STOCK]
    after_partial_sale = project_portfolio(portfolio, entries[:3]).holdings[STOCK]
    after_full_exit = project_portfolio(portfolio, entries[:4])
    after_fresh_buy = project_portfolio(portfolio, entries).holdings[STOCK]

    assert after_purchase.total_cost is None
    assert after_partial_sale.total_cost is None
    assert STOCK not in after_full_exit.holdings
    assert after_fresh_buy.total_cost == Decimal("14")


def test_cash_flow_and_income_remain_distinct_and_both_change_cash() -> None:
    portfolio = make_portfolio()
    contribution = make_entry(portfolio.id, 1, LedgerEntryType.CASH_FLOW, CashFlow("CNY", Decimal("100")))
    dividend = make_entry(
        portfolio.id,
        2,
        LedgerEntryType.INCOME,
        Income("CNY", Decimal("12"), tax=Decimal("1"), other_fee=Decimal("0.5"), instrument=STOCK),
    )

    state = project_portfolio(portfolio, [contribution, dividend])

    assert contribution.entry_type is LedgerEntryType.CASH_FLOW
    assert dividend.entry_type is LedgerEntryType.INCOME
    assert state.cash == {"CNY": Decimal("110.5")}


def test_internal_position_transfer_conserves_known_quantity_and_cost() -> None:
    source = make_portfolio("source")
    destination = make_portfolio("destination")
    operation_id = "shared-transfer"
    source_entries = [
        make_entry(
            source.id, 1, LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("10"), Decimal("100"))
        ),
        make_entry(
            source.id,
            2,
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.OUT, Decimal("4"), Decimal("40")),
            operation_id=operation_id,
        ),
    ]
    destination_entry = make_entry(
        destination.id,
        1,
        LedgerEntryType.TRANSFER,
        PositionTransfer(STOCK, TransferDirection.IN, Decimal("4"), Decimal("40")),
        operation_id=operation_id,
    )

    source_state = project_portfolio(source, source_entries)
    destination_state = project_portfolio(destination, [destination_entry])

    assert source_entries[1].operation_id == destination_entry.operation_id
    assert source_state.holdings[STOCK].quantity + destination_state.holdings[STOCK].quantity == Decimal("10")
    assert source_state.holdings[STOCK].total_cost + destination_state.holdings[STOCK].total_cost == Decimal("100")


def test_position_transfer_preserves_unknown_cost() -> None:
    source = make_portfolio("source")
    destination = make_portfolio("destination")
    source_entries = [
        make_entry(source.id, 1, LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("5"), None)),
        make_entry(
            source.id,
            2,
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.OUT, Decimal("5"), None),
        ),
    ]
    destination_entry = make_entry(
        destination.id,
        1,
        LedgerEntryType.TRANSFER,
        PositionTransfer(STOCK, TransferDirection.IN, Decimal("5"), None),
    )

    assert STOCK not in project_portfolio(source, source_entries).holdings
    assert project_portfolio(destination, [destination_entry]).holdings[STOCK].total_cost is None


def test_position_and_cash_adjustments_assert_resulting_state() -> None:
    portfolio = make_portfolio()
    entries = [
        make_entry(portfolio.id, 1, LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("10"), None)),
        make_entry(portfolio.id, 2, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("100"))),
        make_entry(
            portfolio.id,
            3,
            LedgerEntryType.ADJUSTMENT,
            PositionAdjustment(STOCK, Decimal("12"), Decimal("144"), "custodian statement"),
        ),
        make_entry(
            portfolio.id,
            4,
            LedgerEntryType.ADJUSTMENT,
            CashAdjustment("CNY", Decimal("95"), "bank statement"),
        ),
    ]

    state = project_portfolio(portfolio, entries)

    assert state.holdings[STOCK].quantity == Decimal("12")
    assert state.holdings[STOCK].total_cost == Decimal("144")
    assert state.cash == {"CNY": Decimal("95")}


def test_void_removes_wrong_entry_and_replacement_supplies_truth() -> None:
    portfolio = make_portfolio()
    wrong = make_entry(portfolio.id, 1, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("100")))
    void = make_entry(portfolio.id, 2, LedgerEntryType.VOID, Void(wrong.entry_id, "wrong amount"))
    replacement = make_entry(portfolio.id, 3, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("80")))

    state = project_portfolio(portfolio, [wrong, void, replacement])

    assert state.cash == {"CNY": Decimal("80")}


def test_void_target_must_be_earlier() -> None:
    portfolio = make_portfolio()
    target = make_entry(portfolio.id, 2, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("100")))
    void = make_entry(portfolio.id, 1, LedgerEntryType.VOID, Void(target.entry_id, "not earlier"))

    with pytest.raises(InvalidVoidError, match="earlier"):
        project_portfolio(portfolio, [target, void])


def test_entry_cannot_be_voided_twice() -> None:
    portfolio = make_portfolio()
    target = make_entry(portfolio.id, 1, LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("100")))
    first = make_entry(portfolio.id, 2, LedgerEntryType.VOID, Void(target.entry_id, "first correction"))
    second = make_entry(portfolio.id, 3, LedgerEntryType.VOID, Void(target.entry_id, "second correction"))

    with pytest.raises(InvalidVoidError, match="already voided"):
        project_portfolio(portfolio, [target, first, second])


def test_backdated_entry_replays_by_effective_time_then_sequence() -> None:
    portfolio = make_portfolio()
    opening = make_entry(
        portfolio.id,
        1,
        LedgerEntryType.OPENING_POSITION,
        OpeningPosition(STOCK, Decimal("10"), Decimal("100")),
        effective_at=NOW,
    )
    later_sale = make_entry(
        portfolio.id,
        2,
        LedgerEntryType.TRADE,
        Trade(STOCK, TradeSide.SELL, Decimal("10"), Decimal("30")),
        effective_at=NOW + timedelta(days=2),
    )
    backdated_buy = make_entry(
        portfolio.id,
        3,
        LedgerEntryType.TRADE,
        Trade(STOCK, TradeSide.BUY, Decimal("10"), Decimal("20")),
        effective_at=NOW + timedelta(days=1),
    )

    holding = project_portfolio(portfolio, [opening, later_sale, backdated_buy]).holdings[STOCK]

    assert holding.quantity == Decimal("10")
    assert holding.total_cost == Decimal("150")
    assert holding.average_cost == Decimal("15")


@pytest.mark.parametrize(
    ("entry_type", "payload"),
    [
        (LedgerEntryType.OPENING_CASH, OpeningCash("USD", Decimal("1"))),
        (LedgerEntryType.CASH_FLOW, CashFlow("USD", Decimal("1"))),
        (LedgerEntryType.INCOME, Income("USD", Decimal("1"))),
        (LedgerEntryType.TRANSFER, CashTransfer("USD", TransferDirection.IN, Decimal("1"))),
        (LedgerEntryType.ADJUSTMENT, CashAdjustment("USD", Decimal("1"), "correction")),
    ],
)
def test_explicit_cash_currency_must_match_portfolio_base_currency(entry_type, payload) -> None:
    portfolio = make_portfolio(currency="CNY")
    entry = make_entry(portfolio.id, 1, entry_type, payload)

    with pytest.raises(CurrencyMismatchError, match="base currency CNY"):
        project_portfolio(portfolio, [entry])


def test_decimal_projection_does_not_use_binary_float_arithmetic() -> None:
    portfolio = make_portfolio()
    entries = [
        make_entry(
            portfolio.id,
            1,
            LedgerEntryType.TRADE,
            Trade(STOCK, TradeSide.BUY, Decimal("0.1"), Decimal("0.2")),
        ),
        make_entry(portfolio.id, 2, LedgerEntryType.CASH_FLOW, CashFlow("CNY", Decimal("0.03"))),
    ]

    state = project_portfolio(portfolio, entries)

    assert state.holdings[STOCK].total_cost == Decimal("0.02")
    assert state.cash == {"CNY": Decimal("0.01")}
