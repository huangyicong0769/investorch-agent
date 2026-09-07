from datetime import UTC, datetime
from decimal import Decimal

from investorch.portfolio.domain import Broker, BrokerAccount
from investorch.portfolio.schema import init_portfolio_storage
from investorch.portfolio.storage import (
    create_broker,
    create_broker_account,
    get_broker,
    get_broker_account,
    list_broker_accounts,
    list_brokers,
)

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def test_registered_broker_and_account_roundtrip(tmp_path):
    db = tmp_path / "portfolio.db"
    init_portfolio_storage(db)
    broker = Broker("b", "qmt", "Broker", NOW, NOW, {"region": "cn"})
    account = BrokerAccount("a", "b", "external", "Trading", "stock", NOW, NOW, {"tags": ["primary"]})
    create_broker(db, broker)
    create_broker_account(db, account)
    assert get_broker(db, "b") == broker
    assert get_broker_account(db, "a") == account
    assert list_brokers(db) == [broker]
    assert list_broker_accounts(db, broker_id="b") == [account]


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


def test_account_attribution_persists_and_rebuilds_aggregate(tmp_path):
    from investorch.portfolio.domain import LedgerEntry, LedgerEntryType, OpeningCash, Portfolio
    from investorch.portfolio.storage import (
        append_ledger_operation,
        create_portfolio,
        get_broker_account_portfolio_state,
        get_portfolio_state_with_attribution,
        list_ledger_entries,
        rebuild_portfolio_projection,
    )

    db = tmp_path / "portfolio.db"
    init_portfolio_storage(db)
    create_broker(db, Broker("b", "qmt", "Broker", NOW, NOW))
    create_broker_account(db, BrokerAccount("a", "b", "external", "Trading", "stock", NOW, NOW))
    create_portfolio(db, Portfolio("p", "Portfolio", "CNY", NOW, NOW))
    entries = [
        LedgerEntry(
            str(n),
            "op",
            "p",
            n,
            LedgerEntryType.OPENING_CASH,
            NOW,
            NOW,
            "test",
            OpeningCash("CNY", Decimal(amount)),
            broker_account_id=account,
        )
        for n, amount, account in [(1, 100000, "a"), (2, 50000, None)]
    ]
    append_ledger_operation(db, entries)
    state = get_portfolio_state_with_attribution(db, "p")
    assert state.aggregate.cash == {"CNY": Decimal(150000)}
    assert state.accounts["a"].cash == {"CNY": Decimal(100000)}
    assert state.accounts[None].cash == {"CNY": Decimal(50000)}
    assert get_broker_account_portfolio_state(db, "p", "a") == state.accounts["a"]
    assert list_ledger_entries(db, "p") == entries
    assert rebuild_portfolio_projection(db, "p") == state.aggregate
    assert get_portfolio_state_with_attribution(db, "p") == state


def test_broker_identity_conflicts_and_invalid_metadata_fail_closed(tmp_path):
    import pytest

    from investorch.portfolio.domain import PortfolioDomainError
    from investorch.portfolio.schema import PortfolioConflictError

    db = tmp_path / "portfolio.db"
    init_portfolio_storage(db)
    create_broker(db, Broker("b", "qmt", "Broker", NOW, NOW))
    with pytest.raises(PortfolioConflictError):
        create_broker(db, Broker("b", "qmt", "Duplicate", NOW, NOW))
    account = BrokerAccount("a", "b", "external", "Trading", "stock", NOW, NOW)
    create_broker_account(db, account)
    with pytest.raises(PortfolioConflictError):
        create_broker_account(db, BrokerAccount("other", "b", "external", "Duplicate", "stock", NOW, NOW))
    with pytest.raises(PortfolioConflictError):
        create_broker_account(db, BrokerAccount("missing", "unknown", "external", "Missing", "stock", NOW, NOW))
    for metadata in ({"nested": {1: "invalid"}}, {"nan": float("nan")}, {"tuple": (1, 2)}):
        with pytest.raises(PortfolioDomainError):
            Broker("invalid", "qmt", "Broker", NOW, NOW, metadata)
    assert get_broker_account(db, "a") == account


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


def test_v1_migration_keeps_legacy_ledger_and_aggregate_unallocated(tmp_path):
    import sqlite3
    from pathlib import Path

    from investorch.portfolio.storage import get_portfolio_state_with_attribution, list_ledger_entries

    db = tmp_path / "portfolio.db"
    with sqlite3.connect(db) as connection:
        connection.executescript((Path(__file__).parent / "fixtures" / "portfolio_schema_v1.sql").read_text())
        connection.execute(
            "INSERT INTO portfolios VALUES (?, ?, NULL, ?, ?, NULL, NULL, ?, ?)",
            ("p", "Legacy", "ACTIVE", "CNY", NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute("INSERT INTO portfolio_cash VALUES (?, ?, ?)", ("p", "CNY", "123.45"))
        connection.execute(
            "INSERT INTO portfolio_holdings VALUES (?, ?, ?, ?, ?)", ("p", "600000", "XSHG", "100", "1500")
        )
        connection.execute(
            "INSERT INTO portfolio_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                "1",
                "p",
                "op",
                1,
                "OPENING_CASH",
                NOW.isoformat(),
                NOW.isoformat(),
                "legacy",
                '{"currency": "CNY", "amount": "123.45"}',
            ),
        )
    init_portfolio_storage(db)
    init_portfolio_storage(db)
    state = get_portfolio_state_with_attribution(db, "p")
    assert state.aggregate.cash == {"CNY": Decimal("123.45")}
    assert next(iter(state.aggregate.holdings.values())).total_cost == Decimal(1500)
    assert state.accounts[None].cash == state.aggregate.cash
    assert state.accounts[None].holdings == state.aggregate.holdings
    assert list_ledger_entries(db, "p")[0].broker_account_id is None
    assert list_brokers(db) == []
    assert list_broker_accounts(db) == []


def test_sqlite_rejects_duplicate_allocated_and_unallocated_projection_rows(tmp_path):
    import sqlite3

    import pytest

    from investorch.portfolio.domain import Portfolio
    from investorch.portfolio.storage import create_portfolio

    db = tmp_path / "portfolio.db"
    init_portfolio_storage(db)
    create_portfolio(db, Portfolio("p", "Portfolio", "CNY", NOW, NOW))
    create_broker(db, Broker("b", "qmt", "Broker", NOW, NOW))
    create_broker_account(db, BrokerAccount("a", "b", "external", "Trading", "stock", NOW, NOW))
    with sqlite3.connect(db) as connection:
        for account in ("a", None):
            holdings = ("p", account, "600000", "XSHG", "100", "1500")
            cash = ("p", account, "CNY", "1000")
            connection.execute("INSERT INTO portfolio_account_holdings VALUES (?, ?, ?, ?, ?, ?)", holdings)
            connection.execute("INSERT INTO portfolio_account_cash VALUES (?, ?, ?, ?)", cash)
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO portfolio_account_holdings VALUES (?, ?, ?, ?, ?, ?)", holdings)
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO portfolio_account_cash VALUES (?, ?, ?, ?)", cash)


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


def test_unknown_account_rolls_back_entire_ledger_operation(tmp_path):
    import pytest

    from investorch.portfolio.domain import LedgerEntry, LedgerEntryType, OpeningCash, Portfolio
    from investorch.portfolio.schema import PortfolioConflictError
    from investorch.portfolio.storage import (
        append_ledger_operation,
        create_portfolio,
        get_portfolio_state_with_attribution,
        list_ledger_entries,
    )

    db = tmp_path / "portfolio.db"
    init_portfolio_storage(db)
    create_portfolio(db, Portfolio("p", "Portfolio", "CNY", NOW, NOW))
    entries = [
        LedgerEntry(
            str(n),
            "op",
            "p",
            n,
            LedgerEntryType.OPENING_CASH,
            NOW,
            NOW,
            "test",
            OpeningCash("CNY", Decimal(100)),
            broker_account_id=account,
        )
        for n, account in [(1, None), (2, "missing")]
    ]
    with pytest.raises(PortfolioConflictError):
        append_ledger_operation(db, entries)
    assert list_ledger_entries(db, "p") == []
    assert get_portfolio_state_with_attribution(db, "p").aggregate.cash == {}
    assert get_portfolio_state_with_attribution(db, "p").accounts == {}
