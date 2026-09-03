# Portfolio Persistence

InvestOrch stores Portfolio business state in `<state>/portfolio.db`. This database is separate from
`<state>/sessions.db`: session deletion, archive, clear, fork, and compaction do not modify Portfolio data.

## Storage contract

`portfolio.db` uses SQLite's integer `PRAGMA user_version` as an independent schema version. A new database is
created directly at the latest canonical schema. Future upgrades migrate each supported older schema directly to
the current schema; they may rewrite every Ledger payload and rebuild projections. There is no per-payload version.
An unversioned non-empty database and a database newer than the application are rejected rather than interpreted.

Portfolio metadata, including an optional workspace-relative strategy binding, is stored relationally. Each Ledger
entry has relational identity, ordering, timing, source, and external-reference columns plus one canonical JSON
payload. Financial `Decimal` values are encoded as exact text and are never stored as SQLite `REAL` values.

The append-only Ledger is authoritative. Holdings and logical Cash are relational materialized projections for
ordinary reads, not independent truth. Each Ledger mutation fully replays every affected Portfolio through the A0
projector, then replaces its projection rows in the same transaction. A public rebuild operation provides the same
repair path from persisted Ledger history.

Writes use short `BEGIN IMMEDIATE` transactions limited to local persistence work. One Ledger operation may span
multiple Portfolios; all Ledger rows and all affected projections commit or roll back together. A1 deliberately uses
complete replay instead of incremental projection maintenance, including for backdated entries and VOID corrections.

## Scope

A1 provides persistence primitives only. It does not add Portfolio application workflows, Agent Tools, approvals,
Web or TUI features, Broker/QMT data, account links, reconciliation, target state, strategy execution, market data,
NAV or performance history, tax lots, FX accounting, or a generic ORM/repository framework.
