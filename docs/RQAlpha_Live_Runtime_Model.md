# RQAlpha live market runtime

This document defines the Core/Windows execution contract for remote control and reliable fact delivery. The companion stages frozen strategies and runs approved daily stock Strategies in spawned RQAlpha workers connected to real MiniQMT data. Trading is deliberately unavailable: orders reaching its rejection-only broker become REJECTED without fills. See the [remote execution protocol](QMT_Remote_Execution_Protocol.md) for configuration and HTTP contracts.

## Ownership and distribution boundaries

Core owns the canonical Portfolio Ledger, account attribution, immutable deployment metadata, private strategy artifacts, and bootstrap production. The existing Windows `investorch-qmt` companion owns the RQAlpha runtime foundation and independently validates bootstrap messages.

Both distributions pin `rqalpha==6.3.0`. Each has its own `pyproject.toml`, lockfile, tests, and package build. Neither imports or depends on the other distribution. There is no shared Python DTO package or additional execution distribution.

```text
Core                                      Windows investorch-qmt
Portfolio Ledger                          authenticated MCP/HTTP parent service
  → account-local projections               get_status: market / trading / control
  → aggregate PortfolioState
LiveDeployment + private strategy         independent Bootstrap V1 parser
  → exact bootstrap wire document           → InvestOrchLiveMod
                                             → native RQAlpha Portfolio/Account/Position
live TRADE ingestion                      orthogonal runtime safety state
```

The distributions communicate through JSON over authenticated REST and MCP on the same companion server. Core owns the coordinator; the companion ExecutionNodeService owns staging, its control session, and durable outbox. MCP exposes semantic start/stop tools, while REST carries artifacts, heartbeats, facts, and ACKs. `ACTIVE` in Core does not claim that a Windows process is running or that QMT is connected.

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
    bootstrap.json  # persisted by Core before the first staging request
```

This directory is private Core state rather than mutable Workspace content. The deployment persists the original source path, hash, frozen JSON parameters, artifact relative path, exact Portfolio/account identity, and RQAlpha version. Later Workspace edits or StrategyBinding edits do not change an existing deployment. Manifest JSON records the same execution identity and creation evidence; the database remains the deployment registry.

Core computes the hash when freezing and verifies the private artifact before sending its exact bytes as Base64. The companion verifies SHA-256 against the manifest, independently validates Bootstrap V1 and cross-document identity, and atomically stages the deployment. Retrying the same deployment requires identical content. Core persists the original Bootstrap before the first PUT and reuses it, including its original timestamp and Ledger sequence; retries never regenerate a snapshot from newer economic state.

### Persistent lifecycle

| Transition | Meaning |
| --- | --- |
| PREPARED → ACTIVE | Reserve the Portfolio and capture its canonical Ledger head and start time. |
| PREPARED → FAILED | Abandon preparation with a failure reason. |
| ACTIVE → STOPPED | End the persistent reservation normally. |
| ACTIVE → FAILED | End the reservation with a failure reason. |

STOPPED and FAILED are terminal; a new run requires a new deployment. Metadata identity fields have no update operation. A partial unique database index permits at most one ACTIVE deployment per Portfolio. Multiple PREPARED deployments are allowed, and different Portfolios may use the same BrokerAccount.

An ACTIVE Portfolio rejects ordinary economic mutations at the storage write boundary: initialization, manual trade/cash/income/adjustment, correction, transfers involving either Portfolio, and allocation. Metadata and StrategyBinding edits remain allowed because the active artifact is already frozen. Ordinary append cannot spoof `source="live_execution"` to bypass this boundary; live trades use the dedicated ingestion API.

The reservation survives Core restart and does not expire with a network lease. The companion maintains a separate node-wide, in-memory control session: a valid lease cannot be preempted (`CONTROL_SESSION_BUSY`); expiry or explicit close permits a successor. Expiry makes execution control unavailable. MCP state changes carry the current lease in a request-local HTTP header managed by Core infrastructure. This lease does not itself end Core ownership or kill a runtime. Core releases ownership only after observing the matching remote terminal deployment, draining its pending facts, and verifying that the remote ACK cursor equals the canonical Ledger head.

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

The companion's `build_live_config()` accepts the wire snapshot and frozen parameters. It selects live run type, disables simulation, and enables the single `investorch_qmt.rqalpha_live.mod` entry point with native accounts support. The production worker supplies the first complete executable trading date, the daily EventSource, live PriceBoard, and TradingUnavailableBroker; LIVE does not invent a distant end date.

`InvestOrchLiveMod.start_up()` validates the snapshot and sets native account cash and initial-position constructor inputs before RQAlpha constructs its Portfolio. RQAlpha 6.3.0 calls Mod startup before data/date initialization and Portfolio construction. Replacing Portfolio at `POST_SYSTEM_INIT` would risk leaving duplicate accounting listeners, so that event only verifies the constructed cash and quantities.

The runtime adapter reuses native Portfolio, Account, and Position. Cold-start positions use the native previous-close initialization baseline. Their `avg_price` is runtime statistical state, not a reconstruction of InvestOrch historical cost. Core cost is neither sent in V1 nor inverted into a historical execution price. Native `get_state()/set_state()` remains available for later checkpoint/resume work.

Restrictions live at this concrete mapping boundary: the current adapter requires CNY cash, XSHG/XSHE numeric instrument codes, and nonnegative whole-share quantities. Unsupported inputs fail instead of being dropped or relabeled. Conversion to RQAlpha's numeric cash representation occurs only after wire validation, with a finite-range check. These restrictions do not introduce a generic capability registry or claim real broker feasibility.

The accepted scenario is a clean pre-trading cold start with no outstanding broker order or pending event. Mid-session crash recovery, open/partial-order restoration, and T+1 reconciliation are not implemented. Tests construct a real RQAlpha Portfolio with test-local data/environment support; no production fake Broker or EventSource is installed. The production `LIVE_TRADING` main loop uses the real xtdata market adapter and rejection-only broker.

## Runtime safety state

The companion models three independent dimensions:

| Dimension | Values |
| --- | --- |
| Lifecycle | STARTING, RUNNING, STOPPING, STOPPED, FAILED |
| Portfolio sync | SYNCED, COMMIT_PENDING, DESYNCED |
| Required dependency health | AVAILABLE, UNAVAILABLE |

`can_submit_new_order` is true only when lifecycle is RUNNING, Portfolio sync is SYNCED, and a nonempty set of required dependencies is entirely AVAILABLE. Empty dependency evidence fails closed. Successful bootstrap marks sync SYNCED but does not claim RUNNING or healthy dependencies. A cash/quantity mismatch marks FAILED/DESYNCED.

This value object does not monitor dependencies, schedule heartbeats, reconnect, or submit orders. The supervisor applies the control/sync safety gate to worker event delivery. This gate never enables trading: the production broker continues to reject orders.

## Idempotent live trades and late facts

`LiveExecutionOperations.ingest_live_trade()` accepts deployment ID, broker trade ID, typed instrument/side/quantity/price/fees, and effective timestamp. Portfolio and BrokerAccount are resolved from the deployment.

Canonical entries use `source="live_execution"`. `external_ref` is the compact JSON encoding of `[broker_account_id, broker_trade_id]`, avoiding delimiter collisions and namespacing broker identities across accounts. A partial unique index covers this source with non-NULL external references only; unrelated import/manual references remain unrestricted.

An immediate SQLite transaction resolves identity, allocates the next canonical sequence, replays the Ledger, appends the entry, and replaces projections. Identical delivery returns the already committed LedgerEntry and sequence. A different normalized Trade, effective timestamp, Portfolio, or account under the same identity raises `LiveTradeIdempotencyConflict`. Different JSON formatting or numerically equal Decimal representations do not create a second economic effect.

PREPARED cannot ingest trades. Known ACTIVE, STOPPED, and FAILED deployments may receive a late broker fact, subject to existing canonical Ledger validity. Terminal status is not proof that no real fill can arrive.

The owner explicitly chose to preserve the strict Ledger constraints: an invalid late fact fails visibly and is not appended. For example, a late sale dated before a previously recorded full transfer can make replay insufficient; Core rejects it rather than permitting a negative holding or rewriting history. This rejection does not undo the real broker fill. The coordinator preserves the uncommitted fact in the companion durable outbox and fails synchronization closed; it does not add a second accounting engine or repair broker state. The coordinator delivers only facts matching ACTIVE Core ownership. Receiving an old deployment's late fact after a successor activates requires later broker reconciliation and must not silently attach that fact to the successor.

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

Remote transport and runtime coordination retain Core schema v5, Bootstrap V1, and companion runtime.db v1. TRADE_V1 is unchanged and this market runtime has no production Trade producer. XtQuantTrader, broker callbacks, accounting parity, corporate-action reconciliation, open orders, checkpoint/resume, and automatic worker restart remain outside the implemented capabilities.

## Spawned market worker

The Windows parent owns ASGI/MCP/REST, authority, ExecutionNodeService, runtime.db, outbox, and RuntimeSupervisor. Each deployment gets one process created with `multiprocessing.get_context("spawn")`. The child owns its RQAlpha Environment, Strategy globals, xtdata connection, quote cache, subscriptions, PriceBoard, EventSource, and TradingUnavailableBroker. The child neither reads nor writes runtime.db.

An immutable WorkerLaunchSpec contains deployment/Portfolio/account identity, deployment directory, and expected Strategy SHA-256. It carries no source bytes, token, lease, or full Bootstrap. The child reads and independently revalidates staged `strategy.py`, `manifest.json`, and `bootstrap.json`. Parent-to-child Pipe messages are STOP and SET_GATE; child-to-parent messages report lifecycle/status only. Ticks, quotes, bars, orders, trades, and callback payloads never cross this Pipe.

READY follows actual Strategy import/init, native Portfolio bootstrap, runtime component installation, xtdata connection, subscriptions, bundle/reference preflight, and session eligibility. Transient startup failures clean up the child and preserve STAGED; intrinsic failures become FAILED. PAUSED remains durable RUNNING. Graceful stop unsubscribes and tears down RQAlpha; bounded termination is available. Crashes and orphaned RUNNING rows after companion restart become FAILED without automatic restart.

## Daily data and event contract

The companion pins `xtquant==250807.1.2`. Completed historical queries support XSHG/XSHE common stocks (CS) and indexes (INDX), at `1d` frequency; symbols map strictly to `.SH`/`.SZ`. Execution instruments remain CS only. Desired subscriptions are Strategy universe union nonzero Bootstrap holdings. POST_UNIVERSE_CHANGED subscribes the new set before removing the old subscription. Quote callbacks only normalize/cache data; `xtdata.run()` runs in a child liveness thread. A lack of ticks is not a disconnect signal.

Native `~/.rqalpha/bundle` remains the authority for reference data, instruments, and the CN trading calendar. Each worker freezes the global end of native `available_data_range("1d")` as its cutoff. Completed bars on or before that date use native data; later completed bars use the MiniQMT daily cache. An operator bundle update does not move an existing worker's cutoff; a new worker reads the new coverage. There is no automatic bundle update or configurable bundle path. Core backtest data selection is unchanged.

The data path is `BaseDataSource → FreshDailyDataSource → LiveDataProxy`. Fresh history merges the native raw prefix with the full completed cached tail, orders and deduplicates dates according to the frozen source boundary, applies suspension filtering and a single RQAlpha adjustment pass, then returns the requested count and fields. A missing or invalid fresh range fails closed; it never falls back to stale native data. Listing and suspension evidence distinguish legitimate absent observations from missing data. `skip_suspended=True` retains the native CS positive-volume filtering semantics.

Historical `fields=None` exposes the common daily fields: `datetime`, `open`, `close`, `high`, `low`, `volume`, and `total_turnover`. Historical `limit_up`/`limit_down` requests fail if their requested source cannot supply them; they are not guessed from current instrument metadata. Exact-date raw validation also uses the provider's previous close and suspension evidence. xtdata volume is converted from hands to shares. Provider precision can differ from native data, as described in [Known issues](Known_Issues.md).

`adjust_type="none"` reads no fresh factors. CS `pre` and `post` queries lazily fetch only the required instrument's factor interval, caching successful empty responses and subsequent increments inside the worker. Native cumulative factors through the cutoff are preserved; later xtdata `dr` event multipliers extend that baseline. One RQAlpha `adjust_bars` call applies price adjustment and inverse volume adjustment to the merged raw history. INDX bypasses corporate-action factors. The pre-adjustment origin follows RQAlpha, including an action effective on D when historical bars end at D−1. Factor failures remain explicit (`FRESH_FACTOR_NOT_READY` or `FRESH_FACTOR_INVALID`), never unadjusted fallback. This preserves RQAlpha computation for the supplied factors, not numerical identity between different suppliers' factors; see [the measured limitation](Known_Issues.md#historical-adjustment-factors-differ-between-suppliers).

Live `current_snapshot()` raises LIVE_CURRENT_SNAPSHOT_UNSUPPORTED. xtdata supplies current execution-day state and PriceBoard independently of completed history. Current D bars remain outside signal history, including when the provider cache already contains a forming D row.

The Strategy computes and freezes D signal in BEFORE_TRADING from data ending at D−1. Daily `include_now=False` alone does not prove that cutoff; tests must observe actual data dates. `matching_type=current_bar` concerns matching price/timing, not signal timing. At 14:57 Asia/Shanghai, BAR/handle_bar executes that frozen signal using the current D live bar, which is still forming and is not completed history. A changed current bar must not change the already determined signal.

Only after the last trading period ends does a bounded query wait confirm final D data before AFTER_TRADING and native SETTLEMENT. Daily requests use `period="1d"`, `fill_data=False`, `dividend_type="none"`, exact requested trading date, and finite required fields. Missing D, malformed data, and valid suspension evidence remain distinct. No last-row, previous-day, fill, or synthetic daily-bar fallback is permitted. PriceBoard uses actual quote last price and instrument limit prices; it never substitutes yesterday's close for missing live price.

All orders reaching TradingUnavailableBroker follow RQAlpha's canonical rejection lifecycle with TRADING_BACKEND_NOT_READY. Normal rejection leaves the runtime running, with no Trade, no open order, and no order-induced cash/position change. Native settlement still runs; the runtime is not a broker accounting/reconciliation implementation.

## Historical maintenance and readiness

A node-wide HistorySyncManager schedules a dedicated spawned maintenance process at 16:30 Asia/Shanghai on native-calendar trading days and performs catch-up after companion startup. It enumerates native XSHG/XSHE CS and INDX instruments within their listing intervals, batches completed raw daily downloads, validates the cache, and reports instrument-count progress. Strategy workers never download bars. Normal daily synchronization adds the completed day; catch-up covers the missing range. Only one maintenance child runs at a time. The ASGI parent does not execute blocking SDK downloads.

Bars remain in MiniQMT's cache. InvestOrch does not maintain a historical bar database or persist factors. On restart, cache coverage is validated again. The global `fresh_through` advances only after the entire requested range passes validation; it does not mean every instrument has a normal bar every day. Combined `available_data_range("1d")` ends at the later of the worker's frozen native cutoff and validated fresh coverage.

`historical_data` reports READY, SYNCING, or NOT_READY separately from realtime `market_data`, `trading`, control authority, portfolio synchronization, and worker phase. It includes provider, `native_through`, `fresh_through`, `target_through`, and batch progress while syncing. Core `get_live_status` preserves these distinctions: realtime quotes may be ready while completed history is not.

A failed after-close sync reports NOT_READY and retries in the background; it does not immediately terminate a worker that has completed that day's lifecycle. Before the next required BEFORE_TRADING boundary, combined history must cover the previous trading day. Otherwise the worker fails with `FRESH_HISTORY_NOT_READY`; it neither delays the signal nor replays it after synchronization. Mid-session first start still remains STAGED with SESSION_ALREADY_STARTED.

Historical adjustment concerns prices and statistical volume only. It does not implement broker dividend cash, delivered bonus shares, rights subscriptions, tax, or reconciliation with the canonical Ledger.

## Validation boundary

Automated tests cover runtime timing, rejection, strict data mapping, and lifecycle safety. Windows package smoke imports the pinned xtquant APIs without connecting; it does not establish MiniQMT connectivity. Verify the actual terminal connection, subscriptions, current quotes and exact-date daily queries in the operating environment. A started Strategy should report READY/RUNNING with truthful market-data status and support graceful stop. RUNNING does not indicate that order execution is available: the current Broker rejects orders without producing trades. Full-session validation additionally observes BEFORE_TRADING, the 14:57 callback, finalized daily data, AFTER_TRADING and SETTLEMENT against the real trading-day clock.
