"""Companion schema v1; unrelated to the Core Portfolio schema."""

SCHEMA = (
    """CREATE TABLE remote_deployments (
        deployment_id TEXT PRIMARY KEY,
        portfolio_id TEXT NOT NULL,
        broker_account_id TEXT NOT NULL,
        strategy_sha256 TEXT NOT NULL,
        strategy_artifact_relpath TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        bootstrap_json TEXT NOT NULL,
        bootstrap_schema_version INTEGER NOT NULL CHECK (bootstrap_schema_version = 1),
        bootstrap_ledger_sequence INTEGER NOT NULL CHECK (bootstrap_ledger_sequence >= 0),
        acked_core_sequence INTEGER NOT NULL CHECK (acked_core_sequence >= bootstrap_ledger_sequence),
        status TEXT NOT NULL CHECK (status IN ('STAGED','RUNNING','STOPPED','FAILED')),
        staged_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        ended_at TEXT,
        failure_reason TEXT
    )""",
    """CREATE UNIQUE INDEX remote_deployment_current_portfolio ON remote_deployments(portfolio_id)
        WHERE status IN ('STAGED','RUNNING')""",
    """CREATE TABLE outbox (
        queue_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        fact_id TEXT NOT NULL UNIQUE,
        deployment_id TEXT NOT NULL REFERENCES remote_deployments(deployment_id),
        fact_type TEXT NOT NULL CHECK (fact_type IN ('TRADE_V1')),
        external_fact_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('PENDING','ACKED')),
        created_at TEXT NOT NULL,
        acked_at TEXT,
        committed_ledger_sequence INTEGER,
        CHECK ((status = 'PENDING' AND acked_at IS NULL AND committed_ledger_sequence IS NULL)
            OR (status = 'ACKED' AND acked_at IS NOT NULL AND committed_ledger_sequence IS NOT NULL))
    )""",
    """CREATE UNIQUE INDEX outbox_external_fact_identity
        ON outbox(deployment_id,fact_type,external_fact_id)""",
)
