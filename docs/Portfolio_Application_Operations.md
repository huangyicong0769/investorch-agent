# Portfolio Application Operations

## Purpose and layering

A2 provides the asynchronous business/use-case API for Portfolio reads and mutations. Future Agent and UI adapters
call `PortfolioOperations`, which validates application preconditions, constructs complete Ledger entries, and
delegates persistence to A1. A0 remains the authority for economic validation and projection; A1 remains the
authority for SQLite transactions, append-only history, and materialized projections.

A2 exposes explicit commands for Portfolio lifecycle, initialization, trades, cash flows, income, adjustments,
corrections, and internal transfers. It does not expose generic Ledger append or payload commands.

## Portfolio lifecycle

Portfolio creation generates its identity and timestamps and always creates an `ACTIVE` Portfolio. Metadata updates
may change the name, description, or strategy binding, but not identity, creation time, status, or base currency.
Archive and restore are explicit lifecycle commands and do not write Ledger entries.

An `ARCHIVED` Portfolio is frozen. It remains readable and may be restored, but metadata and economic mutations are
rejected until restoration.

## Opening state and economic commands

Initialization is a one-shot operation for an existing active Portfolio whose Ledger is empty. Opening cash, when
present, is recorded first in the Portfolio base currency, followed by opening positions in caller order. All opening
entries commit atomically as one operation. Initialization cannot be used once any Ledger history exists.

Ongoing commands record supplied business facts as typed A0 payloads: trades, signed external cash flows, income,
position adjustments, and cash adjustments. A2 does not calculate cost basis, profit and loss, entitlement, market
prices, or adjustment deltas; A0 validates and projects the supplied facts.

Correction is distinct from adjustment. Correcting a wrong entry appends a `VOID` for the existing non-VOID target
and exactly one ordinary replacement entry in the same atomic operation. The original row remains immutable. Unless
the caller supplies a corrected economic time, the replacement retains the target's `effective_at`; the VOID always
uses the target's economic time.

## Atomic internal transfers

Position and cash transfers are paired Portfolio-to-Portfolio commands. They append `OUT` and `IN` entries with one
operation identity through one A1 transaction, so both sides commit or roll back together. Both Portfolios must be
active, distinct, and use the same base currency. Position quantity and transferred cost are caller-supplied business
facts; A2 performs no cost inference or FX conversion.

## Ledger metadata and time

A2 generates a random UUID identity for each Portfolio, Ledger operation, and Ledger entry. It captures one UTC
`recorded_at` when a mutating command begins. An omitted `effective_at` defaults to that command time; an explicitly
supplied economic time is preserved. Every entry created by one command shares its operation identity, recorded time,
opaque non-empty source, and optional opaque external reference.

For each affected Portfolio, A2 reads the persisted Ledger and assigns new sequences immediately after its current
maximum. Multi-entry commands receive increasing values in semantic entry order, while each Portfolio in a transfer
has an independent sequence.

If A1 reports that persisted append order advanced before commit, A2 re-reads every affected Ledger, changes only the
assigned sequences, and retries the whole business operation. Operation identity, entry identities, payloads, source,
external reference, and timestamps remain stable. Only this typed sequence conflict is retryable, for at most three
total append attempts and without backoff; exhaustion raises a typed A2 error.

## Scope boundary

A2 does not add Agent Tools, approvals or permissions, ApplicationHost wiring, Web/TUI APIs, Broker/QMT integration,
account links or reconciliation, external-reference idempotency, target state, strategy execution, market data,
valuation, NAV or performance persistence, tax lots, FX accounting, Ledger editing or deletion, hard Portfolio
deletion, or a generic service, repository, command bus, or unit-of-work framework.
