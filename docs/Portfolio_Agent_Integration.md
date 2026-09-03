# Portfolio Agent Integration

## Purpose and layering

A3 exposes the A2 Portfolio business API to the main InvestOrch Agent through explicit local tools. The dependency
direction is Main Agent → Portfolio tool adapter → `PortfolioOperations` → Portfolio persistence → Portfolio domain.
Tools translate Agent-facing JSON values to typed business inputs and serialize typed results; they do not allocate
Ledger sequence, implement transactions, project state, or write SQLite directly.

One `PortfolioOperations` instance is created by the application host, exposed as `ApplicationHost.portfolios`, and
passed through `AgentLoop` into every `AgentContext`. No database connection or module-global Portfolio service is
kept in Agent context.

## Tool surface and approval

Read tools list Portfolios, return one Portfolio with its current logical state, and return a bounded recent Ledger.
Ledger reads default to 50 entries, accept 1 through 200, select the newest entries, return them in ascending audit
sequence, and report total, returned, and older-history metadata.

Mutation tools explicitly create, update, archive, restore, and initialize Portfolios; record trades, external cash
flows, income, and position or cash adjustments; correct one wrong Ledger entry; and transfer positions or cash
between two Portfolios. Every durable mutation uses the existing Agents SDK approval mechanism. Read tools require no
approval. A3 adds no Portfolio-specific permission policy or approval flow.

Agent-originated mutations always call A2 with `source="agent"` and `external_ref=None`. Tool schemas do not expose
those fields or any operation id, entry id, sequence, or recorded timestamp.

## Exact Agent boundary

Financial inputs are decimal strings converted directly to `Decimal`; outputs are exact strings, while unknown cost
is JSON null. Values are never converted through JSON or Python floats, quantized, or normalized. Instruments use the
provider-neutral `code + market` identity without inference or security-master lookup.

Optional economic timestamps are timezone-aware ISO-8601 strings parsed without attaching a default timezone. A null
timestamp lets A2 use the command time. Malformed or timezone-naive values are rejected at the adapter boundary.

Portfolio and Ledger output is semantic JSON rather than persisted payload JSON. Holdings are sorted by instrument
code and market, cash keys are deterministic, and mutation results contain only operation identity, durable entry
summaries, and resulting logical state.

## Strict correction schema

The locked `openai-agents` 0.22.0 strict-schema path rejects `dict[str, Any]` parameters while constructing the tool:
arbitrary object schemas contain `additionalProperties`, which strict schema does not accept. A3 therefore uses the
plan's fallback: small adapter-local Pydantic models in a discriminated union with `extra="forbid"`. Strict mode stays
enabled, and representative SDK invocation must reach the Python adapter through the generated schema.

Supported correction replacements are opening cash, opening position, trade, cash flow, income, position adjustment,
and cash adjustment. They are converted to typed A0 payloads before A2 is called. The Agent tool rejects correction of
a `TRANSFER` target because A2 correction is single-Portfolio while an internal transfer is a paired business fact;
A3 does not invent a transfer-correction workflow.

The same strict-schema constraint makes opening positions small typed inputs. Arbitrary strategy parameters cross the
Agent boundary as a JSON object string, are parsed and validated as an object, and then become `StrategyBinding`
parameters. Strict schema is not disabled for either case.

## Agent guidance and scope

The main prompt carries a concise operational Portfolio section. Portfolio is logical investment state, not a Broker
account mirror; its Ledger is append-only truth. Correction replaces a wrong historical fact through VOID plus a new
fact, while adjustment asserts a newly recognized current fact. Trade records an executed fact rather than placing an
order, and external cash flow remains distinct from investment income. This guidance is compact enough that A3 does
not add a bootstrap Portfolio memory template.

A3 adds no Web/TUI Portfolio interface, Broker/QMT account or execution semantics, reconciliation or external-ref
idempotency, target state or rebalancing, strategy execution, market data, valuation, NAV/P&L/performance, tax lots,
FX accounting, hard deletion, raw Ledger append/edit/delete, one-sided transfer, transfer correction, dynamic tool
discovery, or new service/repository framework.
