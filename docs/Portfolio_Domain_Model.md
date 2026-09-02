# Portfolio Domain Model

## Scope and truth

A Portfolio is the user's actual logical investment state. It is independent of Sessions, Agent Memory, Broker accounts, target allocations, and backtest results. InvestOrch 0.2.0 remains a single-investor application; user preferences and soft constraints remain in durable Agent Memory rather than a structured user or ownership model.

The append-only Portfolio Ledger is authoritative history. Current Holdings and logical Cash are deterministic projections rebuilt from active Ledger entries. A0 is a pure domain layer and does not persist either history or projections.

## Portfolio metadata

A Portfolio has an id, name, optional description, base currency, timestamps, and an `ACTIVE` or `ARCHIVED` status. An archived Portfolio is conceptually read-only and may later be restored; enforcement belongs to later application and persistence work.

An optional strategy binding contains only a Workspace-relative source path and JSON-compatible parameters. It does not execute or validate a strategy and does not store target holdings or weights.

## Instruments and Broker separation

An instrument is identified by the provider-neutral pair `code + market`, such as `600519 + XSHG`. Provider-specific symbol formatting belongs at integration seams.

Portfolio Holdings are not Broker positions. A future Portfolio may span several Broker accounts, and a Broker account may contain assets allocated to several Portfolios. A0 contains no Broker account, QMT, order, settlement, or reconciliation concepts.

## Logical cash

Cash is tracked by currency as a logical Portfolio balance. It does not represent Broker-specific available, frozen, withdrawable, settlement, or buying-power balances. A `CASH_FLOW` uses a signed amount: positive for external capital entering the Portfolio and negative for capital leaving it.

## Ledger contract

Each immutable Ledger entry records its entry id, operation id, Portfolio id, positive append sequence, entry type, economic `effective_at`, InvestOrch `recorded_at`, opaque source, optional external reference, and typed payload. Entries from one logical operation may share an operation id.

Append/audit order is `sequence`. Active economic entries replay by `effective_at ASC, sequence ASC`, so a backdated entry recomputes all later projections. A Ledger contains these entry types:

- `OPENING_POSITION`: initial quantity and known or unknown total cost.
- `OPENING_CASH`: initial logical cash.
- `TRADE`: an actual buy or sell fill, never an order or proposal.
- `CASH_FLOW`: external capital contribution or withdrawal.
- `INCOME`: investment-generated cash, with gross amount, tax, fees, and optional instrument attribution.
- `TRANSFER`: typed position or cash movement without a market trade.
- `ADJUSTMENT`: a newly recognized real-world resulting position or cash state.
- `VOID`: an append-only declaration that an earlier entry was recorded incorrectly.

A position transfer carries quantity and known or unknown transferred cost. Paired internal transfers can share an operation id and conserve known quantity and cost. A cash transfer analogously moves logical cash without becoming income or a trade.

## Cost projection

Each Holding projection stores instrument, quantity, and `total_cost`; average cost is derived as `total_cost / quantity`. Cost uses moving weighted average:

- a buy adds quantity and gross purchase consideration plus acquisition-side commission, tax, and other fees to known total cost;
- a sell removes quantity and its pre-sale average cost, while cash increases by sale proceeds net of commission, tax, and other fees;
- sale price does not change the average cost of the remaining Holding;
- a fully removed Holding disappears, allowing a later acquisition to start a fresh basis.

Unknown historical cost is represented by `None`, never zero. A known-cost purchase added to an unknown-cost Holding remains unknown, as does a partial sale. A full exit removes that unknown basis; a later fresh known acquisition can establish known cost. A position adjustment may explicitly establish or correct the resulting total cost.

All financial quantities use exact `Decimal` arithmetic. Domain interfaces reject accidental `float` inputs.

## VOID and ADJUSTMENT

A valid `VOID` targets an existing earlier entry in the same Portfolio that has not already been voided. The VOID itself has no economic effect, and the target is excluded from replay without being edited or deleted. Correct truth is appended as an ordinary replacement entry. Invalid target, ordering, Portfolio, and double-VOID relationships are domain errors.

`VOID` means the previous record was wrong. `ADJUSTMENT` means the previous record was correct and a newly recognized real-world state must now be asserted. An event representable as a trade, transfer, cash flow, or income is not an adjustment.

## A0 non-goals

A0 does not introduce Portfolio persistence or `portfolio.db`; repositories or application use cases; Agent Tools or approval integration; Web/TUI UI; Broker/QMT accounts, positions, orders, trades, links, allocation, or reconciliation; strategy execution, signals, targets, or rebalancing; market data, valuation, P&L, NAV, performance, benchmark, or risk analysis; tax lots or double-entry accounting; generic event, investment-data, instrument-registry, or Broker frameworks; or multi-user authentication and RBAC.
