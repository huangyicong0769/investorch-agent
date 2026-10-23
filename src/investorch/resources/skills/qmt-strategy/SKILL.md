---
name: qmt-strategy
description: Write and review Big QMT built-in Python strategy artifacts for human deployment. Use for QMT lifecycle, market data, timing, order semantics, diagnostics, or conversion from a backtest strategy.
version: 1.1.0
---

# Big QMT strategy authoring

Produce a Python artifact for the user to review, deploy, and run inside Big QMT. This Skill supplies no QMT connection, remote execution, order bridge, live deployment, reconciliation, or RQAlpha live runtime.

1. Establish the broker/client version, asset class, strategy period, universe, signal time, intended execution time, and whether the artifact targets backtest or later human-run trading. Keep account identifiers configurable; never invent a real account or embed credentials.
2. Read `references/big-qmt.md` before choosing platform APIs. Use the built-in Python API with injected `ContextInfo`; MiniQMT `xtquant` examples are a different integration. Check the installed client's documentation for version-dependent APIs and enums.
3. Inspect the intended Workspace location with `explore`, then create the source with `edit` (or read before replacing an existing file). Keep source paths Workspace-relative. These are file-authoring tools, not QMT connections.
4. Implement pure signal calculations separately from data access and optional order submission. Use `init(ContextInfo)` for initialization and `handlebar(ContextInfo)` for event logic. State when bars are complete and when actions are eligible. Daily and minute bars require different warmup, session boundaries, and duplicate-event handling.
5. Review data cutoffs and adjustment choice. Historical signals must use information available at the simulated timestamp. Current quote snapshots are not historical observations. Treat missing, stale, suspended, or insufficient data explicitly; do not turn absence into a trade signal.
6. Keep generated artifacts in observation/dry-run mode by default: log proposed intents without calling order functions. When the user requests executable order logic, make activation an explicit human deployment setting and document every order parameter, quantity unit, account, and timing choice. Never run that logic from InvestOrch.
7. Use `exec_command` for local syntax and pure-calculation checks with fixtures, using the application tool’s `command`, `background`, and `timeout_seconds` arguments. Record what requires manual validation in the actual client: callbacks, subscriptions, broker enums, fills, and account permissions. Local tests cannot qualify live execution.
8. Read the saved source back through `explore` and inspect local validation output. A passing syntax check plus verified pure calculations completes local qualification; actual QMT callback/data/order behavior remains unverified until observed in the target client. Deliver source plus a human deployment checklist: correct client/build and Python dependencies; chosen account/mode; instrument and period; data download/coverage; timing and adjustment; dry-run logs; duplicate/restart behavior; order limits and cancellation/rejection handling. The human reviews, deploys, starts, and stops the strategy in Big QMT.

Log strategy version, symbol, bar timestamp, signal inputs, proposed quantity, and callback/error status. Avoid secrets and full account identifiers. A strategy-local flag is neither durable reconciliation nor evidence that an order filled; restarted code must not blindly repeat a pending action.

Read [the complete observation-only example](references/examples.md) before authoring or converting a QMT strategy. It includes a complete source artifact, every Workspace and validation call, fixture results, readback, and the manual client handoff. Use its fixed historical snapshot only for that observation task; a rolling strategy needs an explicit per-bar historical cutoff and completed-bar timing policy.
