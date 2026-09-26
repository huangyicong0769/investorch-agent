---
name: investorch-portfolio
description: Manage logical Portfolio and append-only Ledger workflows. Use for Portfolio lifecycle, executed trades, capital flows, income, adjustments, corrections, transfers, or account attribution questions.
version: 1.0.0
---

# InvestOrch Portfolio

1. Portfolio is InvestOrch's logical investment state, not a Broker or account mirror. Logical cash is not Broker available, frozen, withdrawable, or buying-power cash.
2. Use Portfolio tools for every Portfolio read or mutation. Never edit Portfolio database or Ledger files directly.
3. Ledger history is append-only authoritative truth. Correct a wrong historical entry with correction, which appends a VOID and replacement; use adjustment only to assert newly recognized real-world state.
4. A Portfolio trade records an already-executed economic fact, not an order request. Cash flow is external capital movement; income is investment-generated cash.
5. A Portfolio transfer is a logical movement between two Portfolios. Identify instruments by both code and market.
6. Restore an archived Portfolio before attempting any mutation.
7. Before mutating Portfolio truth, ground every material fact in user-provided or confirmed facts, an established user convention, authoritative data, a stable objective public fact, or deterministic derivation from grounded facts. Suggestions and inferences may remain suggestions, but unsupported assumptions must never be persisted.
8. Clarify missing user, transaction, or accounting facts such as execution price, quantity, fees, historical time, opening values, correction values, adjustment state, transfer cost, Portfolio name, or base currency. Prefer authoritative tools when appropriate; stable facts such as exchange mappings may be verified without needless user reconfirmation. Portfolio UI context identifies only the Portfolio and establishes no economic fact.
9. Use a null effective_at only when the user clearly means a current event or state. Establish the economic time for historical facts; a correction may preserve its target entry's time deterministically.


## Workflow

1. Use `list_portfolios`, `get_portfolio`, and `get_portfolio_ledger` to identify the Portfolio and ground its existing state. Ledger queries are bounded; ask for older evidence when the returned history is incomplete.
2. Choose the semantic operation: initialization records opening state; trade records an executed fact; cash flow records external capital; income records investment-generated cash; adjustment asserts newly recognized state; correction repairs a wrong historical entry using VOID plus replacement. Transfers are paired logical movements, not one-sided cash flows.
3. Supply exact decimal strings and `code + market` identities. Unknown cost remains null. Ground quantity, prices, fees (including zero), taxes, names, currencies, and time; Portfolio UI context establishes identity only. Use timezone-aware ISO-8601 time for historical events.
4. Use the dedicated mutation tool and its normal approval. Restore archived Portfolios before mutation. Correcting a TRANSFER through the single-Portfolio correction tool is unsupported; do not invent a repair through raw Ledger edits.
5. Read resulting logical state and report recorded facts, not broker settlement or order execution claims.

## Strategy binding and account attribution

A strategy binding contains a source path and JSON object parameters. Supply both together to create/change it, or use the explicit clear option. Binding code does not run or deploy the strategy.

Schema v6 separates BrokerAccount identity from logical Portfolio state and retains nullable BrokerAccount attribution on economic facts. A broker account location is not implied by a Portfolio, symbol, strategy binding, or cash balance. When location matters and evidence is ambiguous, clarify it rather than guessing. Current Portfolio Agent tools do not expose a broker_account_id argument or BrokerAccount management surface: respect their schema, report the limitation, and never bypass it by writing SQLite. Existing attribution must not be reinterpreted as a live account mirror.
