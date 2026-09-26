from datetime import UTC, datetime
from decimal import Decimal

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def test_account_projection_sells_only_its_own_cost_and_keeps_legacy_bucket():
    from investorch.portfolio.domain import (
        InstrumentId,
        LedgerEntry,
        LedgerEntryType,
        OpeningPosition,
        Portfolio,
        Trade,
        TradeSide,
    )
    from investorch.portfolio.ledger import project_portfolio_with_attribution

    p = Portfolio("p", "Portfolio", "CNY", NOW, NOW)
    stock = InstrumentId("600000", "XSHG")

    def entry(sequence, account, payload, kind):
        return LedgerEntry(
            str(sequence), "op", "p", sequence, kind, NOW, NOW, "test", payload, broker_account_id=account
        )

    result = project_portfolio_with_attribution(
        p,
        [
            entry(1, "a", OpeningPosition(stock, Decimal(100), Decimal(1000)), LedgerEntryType.OPENING_POSITION),
            entry(2, "b", OpeningPosition(stock, Decimal(100), Decimal(2000)), LedgerEntryType.OPENING_POSITION),
            entry(3, None, OpeningPosition(stock, Decimal(50), None), LedgerEntryType.OPENING_POSITION),
            entry(4, "a", Trade(stock, TradeSide.SELL, Decimal(100), Decimal(25)), LedgerEntryType.TRADE),
        ],
    )
    assert result.accounts["a"].holdings == {}
    assert result.accounts["b"].holdings[stock].total_cost == Decimal(2000)
    assert result.accounts[None].holdings[stock].quantity == Decimal(50)
    assert result.aggregate.holdings[stock].quantity == Decimal(150)
    assert result.aggregate.holdings[stock].total_cost is None
    assert result.aggregate.cash == {"CNY": Decimal(2500)}


def test_account_sell_cannot_consume_another_accounts_holding():
    import pytest

    from investorch.portfolio.domain import (
        InstrumentId,
        InsufficientPositionError,
        LedgerEntry,
        LedgerEntryType,
        OpeningPosition,
        Portfolio,
        Trade,
        TradeSide,
    )
    from investorch.portfolio.ledger import project_portfolio_with_attribution

    stock = InstrumentId("600000", "XSHG")
    entries = [
        LedgerEntry(
            "1",
            "op",
            "p",
            1,
            LedgerEntryType.OPENING_POSITION,
            NOW,
            NOW,
            "test",
            OpeningPosition(stock, Decimal(100), Decimal(1000)),
            broker_account_id="a",
        ),
        LedgerEntry(
            "2",
            "op",
            "p",
            2,
            LedgerEntryType.TRADE,
            NOW,
            NOW,
            "test",
            Trade(stock, TradeSide.SELL, Decimal(10), Decimal(20)),
            broker_account_id="b",
        ),
    ]
    with pytest.raises(InsufficientPositionError):
        project_portfolio_with_attribution(Portfolio("p", "Portfolio", "CNY", NOW, NOW), entries)


def test_void_must_target_same_account_location():
    import pytest

    from investorch.portfolio.domain import (
        InvalidVoidError,
        LedgerEntry,
        LedgerEntryType,
        OpeningCash,
        Portfolio,
        Void,
    )
    from investorch.portfolio.ledger import project_portfolio_with_attribution

    entries = [
        LedgerEntry(
            "1",
            "op",
            "p",
            1,
            LedgerEntryType.OPENING_CASH,
            NOW,
            NOW,
            "test",
            OpeningCash("CNY", Decimal(1000)),
            broker_account_id="a",
        ),
        LedgerEntry("2", "op", "p", 2, LedgerEntryType.VOID, NOW, NOW, "test", Void("1", "correction")),
    ]
    with pytest.raises(InvalidVoidError, match="BrokerAccount"):
        project_portfolio_with_attribution(Portfolio("p", "Portfolio", "CNY", NOW, NOW), entries)


def test_aggregate_cost_is_sum_of_remaining_account_costs():
    from investorch.portfolio.domain import (
        InstrumentId,
        LedgerEntry,
        LedgerEntryType,
        OpeningPosition,
        Portfolio,
        Trade,
        TradeSide,
    )
    from investorch.portfolio.ledger import project_portfolio_with_attribution

    stock = InstrumentId("600000", "XSHG")
    entries = [
        LedgerEntry(
            "1",
            "op",
            "p",
            1,
            LedgerEntryType.OPENING_POSITION,
            NOW,
            NOW,
            "test",
            OpeningPosition(stock, Decimal(100), Decimal(1000)),
            broker_account_id="a",
        ),
        LedgerEntry(
            "2",
            "op",
            "p",
            2,
            LedgerEntryType.OPENING_POSITION,
            NOW,
            NOW,
            "test",
            OpeningPosition(stock, Decimal(100), Decimal(2000)),
            broker_account_id="b",
        ),
        LedgerEntry(
            "3",
            "op",
            "p",
            3,
            LedgerEntryType.TRADE,
            NOW,
            NOW,
            "test",
            Trade(stock, TradeSide.SELL, Decimal(100), Decimal(25)),
            broker_account_id="a",
        ),
    ]
    result = project_portfolio_with_attribution(Portfolio("p", "Portfolio", "CNY", NOW, NOW), entries)
    assert result.aggregate.holdings[stock].quantity == Decimal(100)
    assert result.aggregate.holdings[stock].total_cost == Decimal(2000)
