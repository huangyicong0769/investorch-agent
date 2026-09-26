-- Literal historical Portfolio schema v1.
-- Source: 763b32e950a309bfefd846137802803b8ad3ad95:src/investorch/portfolio/schema.py
-- Captured from that historical initializer; never from the current migration.
-- The v1 ledger and aggregate projections have no account attribution.
-- Opening cash/holding and subsequent cash flow must become a NULL slice.

BEGIN;

CREATE TABLE portfolios (
                portfolio_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                base_currency TEXT NOT NULL,
                strategy_source_path TEXT,
                strategy_parameters_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (status IN ('ACTIVE', 'ARCHIVED')),
                CHECK (
                    (strategy_source_path IS NULL AND strategy_parameters_json IS NULL)
                    OR
                    (strategy_source_path IS NOT NULL AND strategy_parameters_json IS NOT NULL)
                )
            );

CREATE TABLE portfolio_ledger (
                entry_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                effective_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                source TEXT NOT NULL,
                external_ref TEXT,
                payload_json TEXT NOT NULL,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id),
                UNIQUE (portfolio_id, sequence),
                CHECK (sequence > 0),
                CHECK (
                    entry_type IN (
                        'OPENING_POSITION',
                        'OPENING_CASH',
                        'TRADE',
                        'CASH_FLOW',
                        'INCOME',
                        'TRANSFER',
                        'ADJUSTMENT',
                        'VOID'
                    )
                )
            );

CREATE TABLE portfolio_holdings (
                portfolio_id TEXT NOT NULL,
                instrument_code TEXT NOT NULL,
                market TEXT NOT NULL,
                quantity TEXT NOT NULL,
                total_cost TEXT,
                PRIMARY KEY (portfolio_id, instrument_code, market),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
            );

CREATE TABLE portfolio_cash (
                portfolio_id TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount TEXT NOT NULL,
                PRIMARY KEY (portfolio_id, currency),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
            );

INSERT INTO portfolios VALUES ('fixture-portfolio', 'Historical portfolio', 'Literal historical fixture', 'ACTIVE', 'CNY', 'strategies/fixture.py', '{"window":20}', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00');
INSERT INTO portfolio_ledger VALUES ('entry-1', 'fixture-portfolio', 'operation-1', 1, 'OPENING_CASH', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"amount":"1000.00","currency":"CNY"}');
INSERT INTO portfolio_ledger VALUES ('entry-2', 'fixture-portfolio', 'operation-2', 2, 'OPENING_POSITION', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"instrument":{"code":"510300","market":"SH"},"quantity":"10","total_cost":"100.00"}');
INSERT INTO portfolio_ledger VALUES ('entry-3', 'fixture-portfolio', 'operation-3', 3, 'CASH_FLOW', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"amount":"25.50","currency":"CNY"}');
INSERT INTO portfolio_holdings VALUES ('fixture-portfolio', '510300', 'SH', '10', '100.00');
INSERT INTO portfolio_cash VALUES ('fixture-portfolio', 'CNY', '1025.50');

PRAGMA user_version = 1;
COMMIT;
