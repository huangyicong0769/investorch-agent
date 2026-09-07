# RQAlpha live runtime foundation

This document defines the Core/Windows execution contract introduced in 0.2.0.B1. It describes a cold-start foundation with durable Core ownership and trade ingestion. Connecting MiniQMT, starting a remote strategy, and real live trading remain later work.

## Ownership and distribution boundaries

Core owns the canonical Portfolio Ledger, account attribution, immutable deployment metadata, private strategy artifacts, and bootstrap production. The existing Windows `investorch-qmt` companion owns the RQAlpha runtime foundation and independently validates bootstrap messages.

Both distributions pin `rqalpha==6.3.0`. Each has its own `pyproject.toml`, lockfile, tests, and package build. Neither imports or depends on the other distribution. There is no shared Python DTO package or additional execution distribution.

```text
Core                                      Windows investorch-qmt
Portfolio Ledger                          existing authenticated MCP/HTTP service
  → account-local projections               get_status: QMT not_connected
  → aggregate PortfolioState
LiveDeployment + private strategy         independent Bootstrap V1 parser
  → exact bootstrap wire document           → InvestOrchLiveMod
                                             → native RQAlpha Portfolio/Account/Position
live TRADE ingestion                      orthogonal runtime safety state
```

The arrow between distributions defines a wire contract; B1 does not implement transport. The current companion control plane does not expose start/stop strategy tools. `ACTIVE` in Core does not claim that a Windows process is running or that QMT is connected.

## Broker accounts and canonical accounting

`Broker` and `BrokerAccount` contain identity and configuration metadata. They do not mirror broker cash, positions, orders, or connectivity. External account identity is unique within a broker: `(broker_id, external_account_id)`.

Every Ledger entry has `broker_account_id: str | None`. `None` denotes legacy/unallocated assets; it is not a fabricated BrokerAccount. Historical Ledger entries remain unchanged during migration.

`project_portfolio_with_attribution()` evaluates each account location with the existing Ledger evaluator, then sums the slices. The result contains:

- `aggregate: PortfolioState`, preserving the existing Holdings/Cash API.
- `accounts: dict[str | None, PortfolioAccountState]`, exposing each location's holdings and cash.

A sale consumes quantity and average cost from its own account. It cannot use another account's holding to avoid an insufficient-position error. This account-local cost rule was explicitly selected for attributed assets. For example, account A has 100 shares costing 1,000 and B has 100 costing 2,000. Selling A's 100 leaves aggregate cost 2,000, equal to B's remaining basis. Legacy histories entirely in the NULL bucket retain their original accounting. If any remaining slice has unknown cost, the aggregate cost is unknown.

VOID must target an earlier entry in the same Portfolio and account location. Replay remains ordered by economic `effective_at`, then canonical Ledger `sequence`; sequence itself remains the append/audit order.

SQLite materializes both aggregate and account-aware projections in the Ledger append transaction. Separate partial unique indexes enforce allocated and NULL-bucket uniqueness for instrument and currency rows.

### Explicit allocation

`PortfolioOperations.assign_unallocated_assets(portfolio_id, broker_account_id)` delegates to an immediate SQLite transaction. It appends matching TRANSFER OUT/IN entries in the same Portfolio for every unallocated holding and nonzero cash balance. Both legs carry the same operation ID and exact known/unknown cost. Negative cash uses reversed directions to move the liability without changing aggregate cash.

Allocation preserves aggregate state, does not rewrite Ledger history, and does not automatically run during deployment preparation. Repeating allocation after the assets have moved is a no-op. Concurrent requests serialize at SQLite and cannot move assets twice. Future-effective Ledger state is rejected explicitly rather than inventing a future transfer timestamp for an operation requested now.

### Single execution account

Preparation, activation, and snapshot production require all nonzero assets to belong to the selected BrokerAccount. Unallocated assets or assets at another account cause a clear rejection. There is no implicit account slice, automatic allocation, or multi-account routing. Empty/zero locations do not invalidate eligibility.

## Strategy source and parameter contract

`StrategyBinding` is mutable Workspace metadata. It identifies the source and external strategy configuration the user currently wants to use. A `LiveDeployment` freezes the source bytes and parameters for one execution identity.

The strategy-facing namespace is always:

```python
def init(context):
    params = context.investorch_parameters
    context.lookback = int(params.get("lookback", 20))
```

No parameters means `{}`. Parameters must be a finite JSON object with string object keys; nested arrays/objects are supported. Non-JSON values, nonfinite numbers, and cycles are rejected. Copies detach runtime inputs from caller mutation. Parameters are not callback arguments, broker options, or RQAlpha engine configuration.

Core `run_backtest(..., strategy_parameters=...)` and the companion builder inject the same namespace. Backtest `request.json` records `strategy_parameters`, including `{}` when omitted. A backtest does not create a LiveDeployment or mutate the canonical Portfolio.

RQAlpha 6.3.0's actual configuration boundary matters: pass `extra.context_vars` as a JSON string containing `{"investorch_parameters": ...}`. Its native `parse_config()` decodes this string before `main.run()` assigns the values to StrategyContext. This avoids recursive configuration attribute wrapping changing nested strategy dictionaries. Tests exercise the actual RQAlpha parser and strategy context, rather than assuming that passing an arbitrary dict through the configuration layer preserves its type.

Strategies should use the same source for backtest and live and should not read InvestOrch configuration files themselves. `src/investorch/resources/rqalpha.md.template` documents this contract for strategy authors.

## Immutable deployment and private artifact

Preparation loads a Workspace-relative `.py` file through the shared Core source loader. Absolute paths, Workspace escapes, missing files, and non-Python/non-file targets are rejected. The exact bytes are hashed with SHA-256 and written under:

```text
<AppConfig.state_dir>/live/deployments/<deployment_id>/
    strategy.py
    manifest.json
```

This directory is private Core state rather than mutable Workspace content. The deployment persists the original source path, hash, frozen JSON parameters, artifact relative path, exact Portfolio/account identity, and RQAlpha version. Later Workspace edits or StrategyBinding edits do not change an existing deployment. Manifest JSON records the same execution identity and creation evidence; the database remains the deployment registry.

Core computes the hash when freezing. Future remote transfer must verify the received bytes against that hash once. Repeated starts from the same immutable local artifact do not require a new hash pass. B1 has no upload/download endpoint or remote artifact transfer protocol.

### Persistent lifecycle

| Transition | Meaning |
| --- | --- |
| PREPARED → ACTIVE | Reserve the Portfolio and capture its canonical Ledger head and start time. |
| PREPARED → FAILED | Abandon preparation with a failure reason. |
| ACTIVE → STOPPED | End the persistent reservation normally. |
| ACTIVE → FAILED | End the reservation with a failure reason. |

STOPPED and FAILED are terminal; a new run requires a new deployment. Metadata identity fields have no update operation. A partial unique database index permits at most one ACTIVE deployment per Portfolio. Multiple PREPARED deployments are allowed, and different Portfolios may use the same BrokerAccount.

An ACTIVE Portfolio rejects ordinary economic mutations at the storage write boundary: initialization, manual trade/cash/income/adjustment, correction, transfers involving either Portfolio, and allocation. Metadata and StrategyBinding edits remain allowed because the active artifact is already frozen. Ordinary append cannot spoof `source="live_execution"` to bypass this boundary; live trades use the dedicated ingestion API.

The reservation survives Core restart. It has no heartbeat, expiry, fencing generation, takeover, or process health assertion.

## Bootstrap Snapshot V1

The following is the exact wire shape. The producer emits integer version 1, and the consumer accepts only integer version 1. There is no version negotiation, fallback, or conversion path.

```json
{
  "schema_version": 1,
  "deployment_id": "deployment-123",
  "portfolio_id": "portfolio-123",
  "broker_account_id": "account-123",
  "ledger_sequence": 12,
  "generated_at": "2026-09-07T00:00:00+00:00",
  "base_currency": "CNY",
  "cash": "100000.00",
  "positions": [
    {
      "code": "600519",
      "market": "XSHG",
      "quantity": "100"
    }
  ]
}
```

`cash` and `quantity` are finite decimal strings, never binary JSON floats. The serializer emits fixed-point strings. Positions are ordered deterministically by `(code, market)`. The consumer rejects missing/extra fields, duplicate position identities, noninteger versions/sequences, invalid timestamps, and malformed decimal strings. `generated_at` includes a timezone.

Snapshot production requires an ACTIVE deployment. A single SQLite read transaction reads deployment identity, all account slices for eligibility validation, selected account state, and maximum Ledger sequence. The returned `ledger_sequence` describes that exact snapshot point. The deployment's `bootstrap_ledger_sequence` records its activation point; it is not substituted for the current head on a later snapshot read.

V1 carries no cost/average price, historical PnL, strategy parameters/source/hash, RQAlpha opaque state, broker-account mirror, or open orders. The strategy artifact and its frozen parameters are separate inputs.

## Native RQAlpha bootstrap

The companion's `build_live_config()` accepts the wire snapshot and frozen parameters. It selects live run type, disables simulation, and enables the single `investorch_qmt.rqalpha_live.mod` entry point with native accounts support. The caller must supply session dates and, in future work, real broker/event-source backends.

`InvestOrchLiveMod.start_up()` validates the snapshot and sets native account cash and initial-position constructor inputs before RQAlpha constructs its Portfolio. RQAlpha 6.3.0 calls Mod startup before data/date initialization and Portfolio construction. Replacing Portfolio at `POST_SYSTEM_INIT` would risk leaving duplicate accounting listeners, so that event only verifies the constructed cash and quantities.

B1 reuses native Portfolio, Account, and Position. Cold-start positions use the native previous-close initialization baseline. Their `avg_price` is runtime statistical state, not a reconstruction of InvestOrch historical cost. Core cost is neither sent in V1 nor inverted into a historical execution price. Native `get_state()/set_state()` remains available for later checkpoint/resume work.

Restrictions live at this concrete mapping boundary: the current adapter requires CNY cash, XSHG/XSHE numeric instrument codes, and nonnegative whole-share quantities. Unsupported inputs fail instead of being dropped or relabeled. Conversion to RQAlpha's numeric cash representation occurs only after wire validation, with a finite-range check. These restrictions do not introduce a generic capability registry or claim real broker feasibility.

The accepted scenario is a clean pre-trading cold start with no outstanding broker order or pending event. Mid-session crash recovery, open/partial-order restoration, and T+1 reconciliation are not implemented. Tests construct a real RQAlpha Portfolio with test-local data/environment support; no production fake Broker or EventSource is installed. A full `LIVE_TRADING` main loop still requires the real backends.

## Runtime safety state

The companion models three independent dimensions:

| Dimension | Values |
| --- | --- |
| Lifecycle | STARTING, RUNNING, STOPPING, STOPPED, FAILED |
| Portfolio sync | SYNCED, COMMIT_PENDING, DESYNCED |
| Required dependency health | AVAILABLE, UNAVAILABLE |

`can_submit_new_order` is true only when lifecycle is RUNNING, Portfolio sync is SYNCED, and a nonempty set of required dependencies is entirely AVAILABLE. Empty dependency evidence fails closed. Successful bootstrap marks sync SYNCED but does not claim RUNNING or healthy dependencies. A cash/quantity mismatch marks FAILED/DESYNCED.

This value object does not monitor dependencies, schedule heartbeats, reconnect, or submit orders. Real lifecycle supervision and broker-specific submission constraints remain future integrations.

## Idempotent live trades and late facts

`LiveExecutionOperations.ingest_live_trade()` accepts deployment ID, broker trade ID, typed instrument/side/quantity/price/fees, and effective timestamp. Portfolio and BrokerAccount are resolved from the deployment.

Canonical entries use `source="live_execution"`. `external_ref` is the compact JSON encoding of `[broker_account_id, broker_trade_id]`, avoiding delimiter collisions and namespacing broker identities across accounts. A partial unique index covers this source with non-NULL external references only; unrelated import/manual references remain unrestricted.

An immediate SQLite transaction resolves identity, allocates the next canonical sequence, replays the Ledger, appends the entry, and replaces projections. Identical delivery returns the already committed LedgerEntry and sequence. A different normalized Trade, effective timestamp, Portfolio, or account under the same identity raises `LiveTradeIdempotencyConflict`. Different JSON formatting or numerically equal Decimal representations do not create a second economic effect.

PREPARED cannot ingest trades. Known ACTIVE, STOPPED, and FAILED deployments may receive a late broker fact, subject to existing canonical Ledger validity. Terminal status is not proof that no real fill can arrive.

The owner explicitly chose to preserve the strict Ledger constraints: an invalid late fact fails visibly and is not appended. For example, a late sale dated before a previously recorded full transfer can make replay insufficient; Core rejects it rather than permitting a negative holding or rewriting history. This rejection does not undo the real broker fill. Reliable delivery must preserve the uncommitted fact and stop new trading until reconciliation, in B2/B5. B1 does not add a durable fact inbox or a second accounting engine. Receiving an old deployment's late fact after a successor activates also requires later runtime synchronization/reconciliation; B1 provides no remote notification guarantee.

TRADE is only the first bridge. Dividends, splits, delisting, share transformations, and tax can also change RQAlpha economics; their canonical bridge must be audited before real-live parity acceptance.

## Database evolution and next integrations

| Schema | Change |
| --- | --- |
| 1 | Existing Portfolio metadata, Ledger, aggregate holdings/cash. |
| 2 | Broker/account identity, nullable Ledger attribution, account projections and uniqueness. |
| 3 | Immutable deployment metadata and lifecycle fields. |
| 4 | One ACTIVE deployment per Portfolio via partial unique index. |
| 5 | Broker-account-namespaced live trade identity uniqueness. |

The table records when each feature entered the schema; it is not a sequence of committed upgrade steps. Fresh databases are created directly with canonical v5 tables and indexes. Each supported existing version (1, 2, 3, or 4) upgrades directly to v5 in one `BEGIN IMMEDIATE` transaction. A failure in any late DDL step rolls back the entire upgrade, including its schema version and projection copies. Migration retains all legacy attribution as NULL and preserves aggregate projection data. It does not guess a QMT account.

B2 adds remote transport, artifact verification after transfer, reliable event delivery, and runtime coordination. B3 adds real realtime/event-source support. B4 adds the actual QMT broker, order identities, and callbacks. B5 addresses parity, reconciliation, corporate actions, interruptions, durable uncommitted events, and resume. B1 deliberately leaves `get_status` truthful as QMT `not_connected`.
