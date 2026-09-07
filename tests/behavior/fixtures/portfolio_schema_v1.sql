
            BEGIN IMMEDIATE;

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
            
PRAGMA user_version = 1;
COMMIT;
