from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parents[1] / "behavior" / "fixtures"


def load_legacy_portfolio_fixture(db_path: Path, version: int) -> None:
    """Load independent literal historical SQL, without calling current schema code."""
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript((_FIXTURES / f"portfolio_schema_v{version}.sql").read_text())


def portfolio_semantic_snapshot(db_path: Path) -> dict[str, list[dict[str, object]]]:
    """Capture preserved economics, treating v1 projections as NULL attribution."""
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}

        def rows(table: str) -> list[dict[str, object]]:
            if table not in tables:
                return []
            result = []
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                value = dict(row)
                for key, item in value.items():
                    if key.endswith("_json") and item is not None:
                        value[key] = json.loads(item)
                    elif key in {"quantity", "total_cost", "amount"} and item is not None:
                        value[key] = Decimal(item)
                result.append(value)
            return sorted(result, key=repr)

        snapshot = {
            table: rows(table)
            for table in (
                "portfolios",
                "brokers",
                "broker_accounts",
                "portfolio_ledger",
                "portfolio_holdings",
                "portfolio_cash",
                "portfolio_account_holdings",
                "portfolio_account_cash",
            )
        }
        for entry in snapshot["portfolio_ledger"]:
            entry.setdefault("broker_account_id", None)
        for suffix in ("holdings", "cash"):
            attributed = f"portfolio_account_{suffix}"
            if attributed not in tables:
                snapshot[attributed] = [{**row, "broker_account_id": None} for row in snapshot[f"portfolio_{suffix}"]]
        return snapshot
