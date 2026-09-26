import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from investorch.portfolio import PortfolioSchemaError, init_portfolio_storage
from investorch.portfolio.migration import migrate_portfolio


@pytest.mark.parametrize("replacement", ["'active'", "'ACT IVE'"])
@pytest.mark.parametrize("migrate", [False, True])
def test_check_literal_drift_is_rejected_without_mutation(tmp_path, replacement, migrate):
    db = tmp_path / "portfolio.db"
    fixture = Path(__file__).with_name("fixtures") / "portfolio_schema_v1.sql"
    ddl = fixture.read_text().split("INSERT INTO", 1)[0]
    ddl = ddl.replace("'ACTIVE'", replacement)
    with closing(sqlite3.connect(db)) as connection:
        connection.executescript(ddl + "PRAGMA user_version = 1; COMMIT;")
    before = db.read_bytes()

    with pytest.raises(PortfolioSchemaError, match="shape mismatch"):
        if migrate:
            migrate_portfolio(db)
        else:
            init_portfolio_storage(db)

    assert db.read_bytes() == before
    assert not (tmp_path / "migration-archives").exists()


def test_partial_index_source_literal_case_drift_is_rejected(tmp_path):
    db = tmp_path / "portfolio.db"
    fixture = Path(__file__).with_name("fixtures") / "portfolio_schema_v5.sql"
    sql = fixture.read_text().replace("WHERE source = 'live_execution'", "WHERE source = 'LIVE_EXECUTION'")
    with closing(sqlite3.connect(db)) as connection:
        connection.executescript(sql)
    before = db.read_bytes()

    with pytest.raises(PortfolioSchemaError, match="shape mismatch"):
        migrate_portfolio(db)

    assert db.read_bytes() == before
    assert not (tmp_path / "migration-archives").exists()


def test_sql_keyword_case_and_spacing_remain_compatible(tmp_path):
    db = tmp_path / "portfolio.db"
    fixture = Path(__file__).with_name("fixtures") / "portfolio_schema_v5.sql"
    sql = fixture.read_text().replace("CHECK (", "check  (").replace("WHERE source =", "where  source  =")
    with closing(sqlite3.connect(db)) as connection:
        connection.executescript(sql)

    assert migrate_portfolio(db).target_schema_version == 6
