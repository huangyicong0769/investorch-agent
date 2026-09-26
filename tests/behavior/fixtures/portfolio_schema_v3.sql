-- Literal historical Portfolio schema v3.
-- Source: 9b36db3241b662af9b636ce855dbd2533fffbb17:src/investorch/portfolio/schema.py
-- Captured from that historical initializer; never from the current migration.
-- NULL, Account A and Account B deliberately share an instrument/currency.
-- Account B has unknown cost; aggregate cost must remain unknown.

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
                payload_json TEXT NOT NULL, broker_account_id TEXT
                REFERENCES broker_accounts(broker_account_id),
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

CREATE TABLE brokers (
                broker_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                display_name TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE broker_accounts (
                broker_account_id TEXT PRIMARY KEY,
                broker_id TEXT NOT NULL REFERENCES brokers(broker_id),
                external_account_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                account_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (broker_id, external_account_id)
            );

CREATE TABLE portfolio_account_holdings (
                portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
                broker_account_id TEXT REFERENCES broker_accounts(broker_account_id),
                instrument_code TEXT NOT NULL,
                market TEXT NOT NULL,
                quantity TEXT NOT NULL,
                total_cost TEXT
            );

CREATE UNIQUE INDEX portfolio_account_holdings_allocated
                ON portfolio_account_holdings(portfolio_id, broker_account_id, instrument_code, market)
                WHERE broker_account_id IS NOT NULL;

CREATE UNIQUE INDEX portfolio_account_holdings_unallocated
                ON portfolio_account_holdings(portfolio_id, instrument_code, market)
                WHERE broker_account_id IS NULL;

CREATE TABLE portfolio_account_cash (
                portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
                broker_account_id TEXT REFERENCES broker_accounts(broker_account_id),
                currency TEXT NOT NULL,
                amount TEXT NOT NULL
            );

CREATE UNIQUE INDEX portfolio_account_cash_allocated
                ON portfolio_account_cash(portfolio_id, broker_account_id, currency)
                WHERE broker_account_id IS NOT NULL;

CREATE UNIQUE INDEX portfolio_account_cash_unallocated
                ON portfolio_account_cash(portfolio_id, currency)
                WHERE broker_account_id IS NULL;

CREATE TABLE live_deployments (
                deployment_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
                broker_account_id TEXT NOT NULL REFERENCES broker_accounts(broker_account_id),
                strategy_source_path TEXT NOT NULL,
                strategy_sha256 TEXT NOT NULL,
                strategy_parameters_json TEXT NOT NULL,
                strategy_artifact_relpath TEXT NOT NULL,
                rqalpha_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PREPARED','ACTIVE','STOPPED','FAILED')),
                bootstrap_ledger_sequence INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                failure_reason TEXT
            );

INSERT INTO brokers VALUES ('fixture-broker', 'qmt', 'Fixture broker', '{"region":"CN"}', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00');
INSERT INTO broker_accounts VALUES ('account-a', 'fixture-broker', 'external-a', 'Account A', 'STOCK', '{}', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00');
INSERT INTO broker_accounts VALUES ('account-b', 'fixture-broker', 'external-b', 'Account B', 'STOCK', '{}', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00');
INSERT INTO portfolios VALUES ('fixture-portfolio', 'Historical portfolio', 'Literal historical fixture', 'ACTIVE', 'CNY', 'strategies/fixture.py', '{"window":20}', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00');
INSERT INTO portfolio_ledger VALUES ('entry-1', 'fixture-portfolio', 'operation-1', 1, 'OPENING_CASH', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"amount":"1000.00","currency":"CNY"}', NULL);
INSERT INTO portfolio_ledger VALUES ('entry-2', 'fixture-portfolio', 'operation-2', 2, 'OPENING_POSITION', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"instrument":{"code":"510300","market":"SH"},"quantity":"10","total_cost":"100.00"}', NULL);
INSERT INTO portfolio_ledger VALUES ('entry-3', 'fixture-portfolio', 'operation-3', 3, 'OPENING_CASH', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"amount":"2000.00","currency":"CNY"}', 'account-a');
INSERT INTO portfolio_ledger VALUES ('entry-4', 'fixture-portfolio', 'operation-4', 4, 'OPENING_POSITION', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"instrument":{"code":"510300","market":"SH"},"quantity":"20","total_cost":"200.00"}', 'account-a');
INSERT INTO portfolio_ledger VALUES ('entry-5', 'fixture-portfolio', 'operation-5', 5, 'OPENING_CASH', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"amount":"3000.00","currency":"CNY"}', 'account-b');
INSERT INTO portfolio_ledger VALUES ('entry-6', 'fixture-portfolio', 'operation-6', 6, 'OPENING_POSITION', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"instrument":{"code":"510300","market":"SH"},"quantity":"30","total_cost":null}', 'account-b');
INSERT INTO portfolio_ledger VALUES ('entry-7', 'fixture-portfolio', 'operation-7', 7, 'CASH_FLOW', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'manual', NULL, '{"amount":"25.50","currency":"CNY"}', NULL);
INSERT INTO portfolio_holdings VALUES ('fixture-portfolio', '510300', 'SH', '60', NULL);
INSERT INTO portfolio_cash VALUES ('fixture-portfolio', 'CNY', '6025.50');
INSERT INTO portfolio_account_holdings VALUES ('fixture-portfolio', NULL, '510300', 'SH', '10', '100.00');
INSERT INTO portfolio_account_cash VALUES ('fixture-portfolio', NULL, 'CNY', '1025.50');
INSERT INTO portfolio_account_holdings VALUES ('fixture-portfolio', 'account-a', '510300', 'SH', '20', '200.00');
INSERT INTO portfolio_account_cash VALUES ('fixture-portfolio', 'account-a', 'CNY', '2000.00');
INSERT INTO portfolio_account_holdings VALUES ('fixture-portfolio', 'account-b', '510300', 'SH', '30', NULL);
INSERT INTO portfolio_account_cash VALUES ('fixture-portfolio', 'account-b', 'CNY', '3000.00');
INSERT INTO live_deployments VALUES ('deployment-stopped', 'fixture-portfolio', 'account-a', 'strategies/fixture.py', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '{"window":20}', 'artifacts/fixture.py', '6.1.0', 'STOPPED', 6, '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', NULL);
INSERT INTO live_deployments VALUES ('deployment-failed', 'fixture-portfolio', 'account-b', 'strategies/fixture.py', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '{"window":20}', 'artifacts/fixture.py', '6.1.0', 'FAILED', 6, '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', '2026-09-20T09:00:00+08:00', 'historical fixture failure');

PRAGMA user_version = 3;
COMMIT;
