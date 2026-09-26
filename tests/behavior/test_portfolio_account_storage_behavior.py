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
