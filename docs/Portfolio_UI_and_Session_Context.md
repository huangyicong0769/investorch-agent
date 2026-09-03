# Portfolio UI and Session Context

## Purpose and product boundary

A4 makes Portfolio a first-class visible object while preserving InvestOrch's Agent-first workflow. The Web UI may
browse Portfolio metadata, current projected state, and recent Ledger history, but it exposes no direct Portfolio
mutation controls or write endpoints. Creation, lifecycle changes, initialization, economic facts, corrections, and
transfers continue through the Main Agent, the approved A3 FunctionTools, `PortfolioOperations`, and the existing
Ledger transaction path.

Portfolio pages are object views rather than an analytics dashboard. They show only durable Portfolio facts: name,
status, base currency, logical cash, holdings, strategy binding, and Ledger entries. A4 does not derive market value,
NAV, profit and loss, returns, allocation, risk, or Broker/account balances. Unknown cost remains explicitly unknown.

## Web navigation and Agent workflows

The product sidebar contains a first-class Portfolios entry. `/portfolios` lists active and archived Portfolios as
compact summary cards, and `/portfolios/:portfolioId` presents a read-only detail view with overview, holdings, recent
Ledger, and Ask Agent. Recent Ledger is limited to the newest 50 entries in ascending audit order; rows expose a
semantic summary and expandable metadata and payload fields, including visible VOID targets and reasons.

Ask Agent is an inline control that expands from a compact button. Each submitted Portfolio question creates a fresh
active Agent Session, relates the selected Portfolio to that Session, preserves the user's original text as the only
visible user message, and supplies a separate server-generated run hint identifying the Portfolio. The hint identifies
only the referred Portfolio and establishes no economic fact. Tool schemas continue to require an explicit
`portfolio_id`.

New Portfolio is also an Agent workflow, not a form. One idempotent application operation creates a new Session and
starts the Main Agent with a short application-generated instruction to guide creation and initialization. That
instruction is neither displayed nor journaled as user-authored authorization. The eventual Portfolio creation still
uses the approved A3 `create_portfolio` tool.

## Durable Session relations

`extra_session_metadata` stores an ordered JSON array of related Portfolio IDs. It stores no Portfolio snapshots.
Adding IDs is idempotent and preserves first-reference order; a transfer records source before destination when both
are new. Reads resolve IDs against current Portfolio truth, so archived state and later metadata changes display
honestly.

A relation is added only after a structured interaction:

- successful `get_portfolio` or `get_portfolio_ledger`;
- successful Portfolio mutation, including the created ID from `create_portfolio`;
- successful Portfolio transfer, for both source and destination;
- an explicit Ask Agent submission from Portfolio detail.

`list_portfolios`, browsing index/detail pages, plain-text name mentions, rejected approvals, and failed tool calls do
not add relations. Successful Tool observation belongs to the application/runtime output path; A3 tools and the
Portfolio domain remain unaware of Session metadata.

The relation set is Session-scoped and durable. A new normal Session starts empty; archive and restore preserve it;
fork copies it as an independent ordered set; Session deletion removes it; Portfolio archive does not remove it. A4
adds no active-Portfolio state or manual pin, remove, or reorder controls.

## Review and fact-grounding safety

Automated permission review receives the complete active, user-authored instruction history from durable
`user_message` events and only those `user_steer` events already activated for the reviewed Agent turn, in sequence
order, plus the proposed tool name and arguments. Each reviewed turn uses a frozen instruction-history watermark so
a concurrently submitted steer cannot retroactively authorize an already-pending tool call. Assistant content,
reasoning, tool output, reviewer conclusions, application-generated starter instructions, and future queued or
unactivated inputs are not authorization evidence.

When the instruction history exceeds the configured review budget, a specialized compactor produces a bounded
authorization summary. It preserves active requirements, permissions, prohibitions, corrections, conventions,
confirmed material facts, precise relevant values and identifiers, and necessary ordering semantics while removing
repetition and superseded wording. It neither approves actions nor introduces assistant assumptions. Compaction
failure, empty or invalid output, or an over-budget compacted result fails safe to manual ASK; older instructions are
never silently truncated.

Every material fact persisted to Portfolio truth must be grounded in user-provided or confirmed information, an
established user convention, authoritative data, a stable objective public fact, or deterministic derivation from
grounded facts. The Main Agent may offer ungrounded ideas as suggestions but must clarify missing user, transaction,
or accounting facts before mutation. Stable facts such as standard identifiers or exchange mappings may be verified
without unnecessary user confirmation.

Agent-facing mutation schemas do not turn omitted material fees into confirmed zero. Commission, tax, and other fees
must be supplied explicitly, including an explicit `"0"` when zero is grounded. An omitted `effective_at` remains
valid only for a clearly current event or state; historical facts require an established economic time, while a
correction may deterministically retain its target's effective time.

## A4 non-goals

A4 does not add direct Portfolio editing, Broker/QMT integration, account linking or reconciliation, order placement,
market-data enrichment, performance analytics, tax lots, FX accounting, advanced Ledger search or visualization, a
second conversation surface, a Portfolio-specific Agent, a workflow engine, or manual Session relation management.
