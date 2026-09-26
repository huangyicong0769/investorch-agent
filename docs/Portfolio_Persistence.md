# Portfolio Persistence

InvestOrch stores Portfolio business state in `<state>/portfolio.db`. This database is separate from
`<state>/sessions.db`: session deletion, archive, clear, fork, and compaction do not modify Portfolio data.

## Storage contract

`portfolio.db` uses SQLite's integer `PRAGMA user_version` as an independent schema version. A new database is
created directly at the latest canonical schema. The canonical schema is v6. The v1-v5 transition preserves every historical Ledger field and all existing
projections; v1 projections gain a NULL (unallocated) account location. There is no per-payload version.
An unversioned non-empty database and a database newer than the application are rejected rather than interpreted.

Portfolio metadata, including an optional workspace-relative strategy binding, is stored relationally. Each Ledger
entry has relational identity, ordering, timing, source, and external-reference columns plus one canonical JSON
payload. Financial `Decimal` values are encoded as exact text and are never stored as SQLite `REAL` values.

The append-only Ledger is authoritative. Holdings and logical Cash are relational materialized projections for
ordinary reads, not independent truth. Each Ledger mutation fully replays every affected Portfolio through the A0
account-aware projector, then replaces aggregate and account projection rows in the same transaction. A public rebuild operation provides the same
repair path from persisted Ledger history.

Writes use short `BEGIN IMMEDIATE` transactions limited to local persistence work. One Ledger operation may span
multiple Portfolios; all Ledger rows and all affected projections commit or roll back together. A1 deliberately uses
complete replay instead of incremental projection maintenance, including for backdated entries and VOID corrections.

## BrokerAccount attribution

Broker and BrokerAccount are provider-neutral metadata. Ledger entries optionally identify a BrokerAccount;
NULL is an explicit unallocated location. Account holdings and cash sum to the aggregate. Cost is local to each
account; unknown cost remains unknown. Sales cannot consume another account's position, and VOID must preserve
the target account location. Historical `source="live_execution"` is ordinary audit metadata in v6.

## Migration

```sh
investorch migrate portfolio --dry-run
investorch migrate portfolio
# Optional explicit target for a qualification copy:
investorch migrate portfolio --database /absolute/path/portfolio.db --dry-run
```

The default path comes from AppConfig. This command does not initialize Agent, Web, Skill or QMT runtimes.
Fresh databases create v6 directly. Startup validates v6, automatically backs up/upgrades v1/v2, and rejects v3-v5
with an actionable explicit-migration error. Unknown versions, unknown shapes and inconsistent projections fail closed.

Before migrating a real database, stop InvestOrch and the legacy execution node/companion and independently verify
that no old strategy is running. Keep a separate copy of the whole state directory. PREPARED or ACTIVE deployments
block retirement; migration does not stop remote execution and has no force bypass. Review dry-run before migration.
Dry-run never creates an archive. WAL dry-run is rejected before opening SQLite, since even mode=ro can create WAL/SHM
files; use the old version to checkpoint and switch to DELETE journal mode after stopping writers. Do not use
immutable reads that can omit committed WAL records. Actual migration supports WAL through SQLite's backup API.

Every v1-v5 migration first creates `migration-archives/portfolio-vN-to-v6/<UTC timestamp>/` beside the database:

- `portfolio.db.bak`: complete SQLite backup at the original schema version.
- `live_deployments.jsonl`: terminal legacy deployment rows (empty for v1/v2).
- `legacy_schema.sql`, `manifest.json`, `SHA256SUMS`: independently inspectable structure, counts and checksums.

The archive directory is private and published by atomic rename before any schema mutation. An incomplete archive
or a completed backup may remain after failure. The command compares the archived snapshot with the database after
acquiring its write lock and aborts if another writer changed it. One explicit transaction performs all DDL/DML,
foreign-key/quick checks, projection validation and version advancement. Failure rolls back the entire migration.
v3-v5 retirement removes only the live table and live-only indexes; Portfolio, BrokerAccount, Ledger and projection
rows are retained. Repeating migration on v6 validates it without producing another backup.

To inspect a backup, open it read-only. Recovery to the old application requires stopping all writers and restoring
the complete backup to a separate target or deliberately replacing the database together with its journal state;
never lower user_version or copy only the main file of a running WAL database.

## Scope

This transition adds metadata, account attribution and migration support. It does not add allocation workflows,
account-aware Agent tools, QMT connectivity, trading, live deployment ownership, reconciliation or Skill System.
