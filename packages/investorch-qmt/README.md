# InvestOrch QMT

`investorch-qmt` is the independently installable Windows companion MCP server for InvestOrch. It exposes an authenticated Streamable HTTP boundary that the Core application can use without importing either distribution into the other.

The companion provides remote deployment staging, control leases and durable trade-fact delivery. It runs daily stock Strategies in isolated spawned RQAlpha workers with real MiniQMT/xtdata market data. The trading backend is unavailable: orders reaching the broker are rejected with `TRADING_BACKEND_NOT_READY`, producing no fills.

## Requirements

- Windows
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

InvestOrch Core is not a dependency. Windows installation includes exact-pinned `xtquant==250807.1.2` and `rqalpha==6.3.0`. Starting the service does not require a connected MiniQMT terminal; starting a market worker requires real xtdata connectivity and a ready standard `~/.rqalpha/bundle`.

## Install or develop

Install a built wheel as a standalone uv-managed tool:

```powershell
uv tool install .\investorch_qmt-0.1.0-py3-none-any.whl
investorch-qmt --version
```

From a source checkout:

```powershell
cd packages\investorch-qmt
uv sync --locked --dev
uv run investorch-qmt --version
```

The remaining examples use an installed `investorch-qmt` command. Prefix them with `uv run` when developing from source.

## Initialize and run

Initialize once:

```powershell
investorch-qmt init
```

This creates:

```text
%LOCALAPPDATA%\InvestOrch\QMT\
├── investorch-qmt.toml
└── logs\
    └── investorch-qmt.log
```

`init` generates a high-entropy bearer token and refuses to overwrite an existing configuration. It does not print the token. Retrieve it only through the explicit management command:

```powershell
investorch-qmt token show
```

Start the service:

```powershell
investorch-qmt serve
```

The default listener is `http://127.0.0.1:8765`; the MCP endpoint is `/mcp`. Runtime configuration comes only from `investorch-qmt.toml`.

## Localhost and LAN configuration

The generated configuration is safe by default:

```toml
[server]
host = "127.0.0.1"
port = 8765
allowed_hosts = []
```

To let a Core machine on the same trusted LAN or private VPN connect, explicitly bind a LAN interface and list every Host value clients will use:

```toml
[server]
host = "0.0.0.0"
port = 8765
allowed_hosts = [
    "192.168.1.20:8765",
    "qmt-pc:8765",
]
```

`allowed_hosts` validates the HTTP Host header as a DNS-rebinding defense; it is not a source-IP ACL. The security boundary is a trusted network plus Bearer authentication plus Host validation. Wildcards are rejected. LAN mode is intended only for a trusted local network or private VPN. Do not expose the HTTP endpoint directly to the public Internet; it does not provision TLS, OAuth, or mTLS.

## Configure InvestOrch Core

On the Core machine, add the companion to the existing `mcp.toml` registry:

```toml
[[servers]]
name = "qmt"
enabled = true
transport = "streamable_http"
url = "http://192.168.1.20:8765/mcp"
cache_tools_list = false
require_approval = ["start_live_strategy", "stop_live_strategy"]

[servers.headers]
Authorization = "Bearer ${QMT_MCP_TOKEN}"
```

Store the value printed by `investorch-qmt token show` in the Core's existing `investorch.toml` secret section:

```toml
[qmt]
mcp_server = "qmt"

[secrets]
QMT_MCP_TOKEN = "replace-with-the-companion-token"
```

Do not put the literal token in `mcp.toml` or commit either local configuration.

## Rotate the token

```powershell
investorch-qmt token rotate
```

Rotation atomically updates the configuration and prints the new token. The running process deliberately keeps its startup snapshot: restart `investorch-qmt serve`, then update the corresponding Core secret. Before restart the old token remains active; after restart only the new token is accepted.

## Diagnostics and status truth

All public service routes require `Authorization: Bearer <token>`.

- `GET /healthz` reports only that the companion HTTP/MCP process is ready. QMT can be absent while this returns HTTP 200.
- MCP server information reports the installed `investorch-qmt` name and version.
- MCP `get_status` is read-only and returns `service.status = "ready"` with separate market_data, historical_data and trading observations; service readiness alone does not establish market connectivity.

Operational logs rotate under `%LOCALAPPDATA%\InvestOrch\QMT\logs`. Authorization headers and bearer tokens are not logged.

Service readiness, actual worker market connectivity, completed historical-data readiness, and trading readiness are reported separately. No broker account connection or real order submission is provided.


## Remote execution

`serve` initializes `%LOCALAPPDATA%\InvestOrch\QMT\runtime.db` at companion schema v1. Newer schemas fail closed. Deployment metadata and a durable outbox are retained in SQLite with foreign keys, WAL and transactional writes. There is no Portfolio mirror. Core Portfolio schema remains v5 and Bootstrap remains V1.

The same listener serves `/mcp` and `/api/v1` with the same Bearer token and Host policy. Core derives the REST origin and headers from the referenced MCP profile; it does not need a second URL or token.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/node/status` | Node, control and deployment status without strategy source |
| POST | `/api/v1/control-sessions` | Acquire a node-wide in-memory lease; a valid existing lease returns retryable `409 CONTROL_SESSION_BUSY` |
| POST | `/api/v1/control-sessions/{session_id}/renew` | Renew control lease; optional reconciliation assertion |
| DELETE | `/api/v1/control-sessions/{session_id}` | Close current session; stale close cannot close a newer session |
| PUT | `/api/v1/deployments/{deployment_id}` | Validate and stage exact frozen deployment bytes |
| GET | `/api/v1/facts/next` | Deliver one globally oldest pending fact |
| POST | `/api/v1/facts/{fact_id}/ack` | Atomically acknowledge one fact and advance its deployment cursor |

Stage, pull and ACK require `X-InvestOrch-Control-Session`. Missing, expired or closed authority returns `409 STALE_CONTROL_SESSION`. A valid lease cannot be preempted: another Core may acquire authority only after expiry or explicit close. Sessions expire after the returned `lease_timeout_seconds`; they never survive process restart. Expiry invalidates sync and does not kill a runtime. Core maintains heartbeats only while it has ACTIVE live work.

A lease renewal without a body renews authority only. After draining and comparing the Core canonical head, Core may send:

```json
{"reconciled_deployments":[{"deployment_id":"deployment-a","acked_core_sequence":12}]}
```

The service accepts this assertion only when the deployment has no pending facts and the cursor matches. Session close/expiry clears synchronization; a mismatch is DESYNCED. Ordinary heartbeat cannot restore SYNCED. This handshake does not perform broker reconciliation or adopt an unknown deployment.

Staging validates the exact manifest field set, RQAlpha 6.3.0, Base64-decoded bytes against SHA-256, and independent Bootstrap V1 identities before installing `deployments/<deployment_id>/{strategy.py,manifest.json,bootstrap.json}`. Repeating the identical frozen deployment returns the same summary; changed content or another current deployment for the Portfolio conflicts. A failed database write removes newly installed artifacts. Persisted artifacts are not overwritten to repair corruption automatically.

The internal `ExecutionNodeService.enqueue_trade_fact(payload)` seam accepts strict TRADE_V1 facts for a future real broker callback. There is no enqueue REST endpoint or Agent tool. Numeric fields are finite decimal strings, timestamps include a timezone, and broker trade identity deduplicates exact payloads. The outbox returns one fact, retains ACKED rows, and requires an oldest-only ACK with Core sequence exactly N+1. An identical ACK retry succeeds; a changed sequence conflicts. ACK and the deployment cursor commit in one SQLite transaction.

MCP exposes only `get_status`, `start_live_strategy(portfolio_id)` and `stop_live_strategy(portfolio_id)`. Configure approval for start and stop in Core. Both writes require current authority through the infrastructure-managed `X-InvestOrch-Control-Session` HTTP header; the Agent supplies only `portfolio_id`. Missing or stale authority returns `STALE_CONTROL_SESSION` before deployment or backend validation. `get_status` requires only Bearer authentication. Start requires STAGED, reconciled sync, and a bounded real worker READY handshake before RUNNING. Transient readiness/timeout or mid-session first-start rejection preserves STAGED; intrinsic startup errors fail the deployment. Stop handles STAGED, STARTING, and RUNNING, requests graceful cleanup, and terminates after a bounded timeout if necessary. Repeated stop is idempotent; FAILED remains FAILED. Core releases ACTIVE ownership only after observing the terminal state, draining and reconciling.

The companion runs a production RQAlpha market loop with a rejection-only broker. It does not include actual QMT order placement, fake fills, broker reconciliation, WebSocket transport or TLS/PKI. Do not use this plaintext service across an untrusted LAN or public Internet.


## Market operation and validation

Run first start on a non-trading day or before the first trading session period. Mid-session first start returns SESSION_ALREADY_STARTED; it does not synthesize missed callbacks. Trading dates come from the native bundle calendar, and wall time is Asia/Shanghai. Form D signals from data through D−1 in before_trading; handle_bar executes at 14:57. Final D data is checked only after the last period ends, before after_trading and settlement. `include_now=False` alone is not a signal-cutoff proof.

Provision the standard `~/.rqalpha/bundle` deliberately, for example with `rqalpha download-bundle` followed by `rqalpha check-bundle`. Each worker freezes the bundle's actual global daily coverage date. Older completed history comes from native data; the newer completed tail comes from MiniQMT's cache. Monthly bundle downloads need not already cover yesterday, but combined validated coverage must reach the previous trading day before daily preparation. An existing worker keeps its cutoff when the operator updates the bundle; a new worker observes the new cutoff. The service does not automatically update the bundle or add a custom bundle path. Core backtest data selection is unchanged.

The companion synchronizes completed CS and INDX daily history in a dedicated spawned process at 16:30 Asia/Shanghai on trading days, with startup catch-up and background retries. The native instrument list and calendar define candidates; verified provider identifier and instrument-type capability define the supported fresh scope. Unmapped or unsupported candidates are explicitly reported, and their fresh queries fail with `FRESH_HISTORY_UNSUPPORTED`. Native-only queries remain available. Missing cache data or a failed download never excludes an otherwise supported instrument. See [Known issues](../../docs/Known_Issues.md#native-index-identifiers-and-provider-coverage-differ). Strategy workers read the cache and never download bars. A full batch range must pass validation before `fresh_through` advances. Bars stay in MiniQMT's cache; there is no InvestOrch historical bar database or durable factor store. A failed after-close sync reports NOT_READY without immediately killing a completed day's worker. If history remains incomplete at the next BEFORE_TRADING boundary, the worker fails with `FRESH_HISTORY_NOT_READY`; no late signal or replay is performed.

Live history supports XSHG/XSHE CS and INDX at `1d`; execution instruments remain CS only. The raw native prefix and completed cached tail are merged before one RQAlpha adjustment pass for `pre` or `post`. Fresh CS factors are fetched lazily per instrument and cached within the worker; `none` and INDX require no fresh factors. Empty successful factor responses are cached. Invalid or unavailable factors fail explicitly. Different suppliers' numerical factor values can differ even when RQAlpha adjustment semantics are preserved: read [Known issues](../../docs/Known_Issues.md#historical-adjustment-factors-differ-between-suppliers) before comparing native backtests and live values. Historical adjustment does not implement broker corporate-action accounting.

Live historical `fields=None` returns the common daily fields (datetime, OHLC, volume, total_turnover). A requested historical limit field fails when its source cannot supply it; no current limit or guessed percentage is substituted. Completed history reads raw bars first. Missing CS dates can be resolved only from exact-date provider-filled suspension rows with zero activity, prices anchored to a preceding provider observation, a complete verified index time axis, and no contradiction with raw bars. Unresolved gaps remain errors. Only maintenance can download missing anchor or index history; Strategy reads remain cache-only. Accepted suspended rows are retained by `get_bar` and `history_bars(skip_suspended=False)`, and skipped by the default history filter. Current-day bars are exact-date xtdata queries with `fill_data=False`; they do not enter completed signal history. Live `current_snapshot` remains unsupported. Subscriptions cover universe union Bootstrap holdings and change dynamically. See [the runtime contract](../../docs/RQAlpha_Live_Runtime_Model.md#daily-data-and-event-contract) for evidence limitations, adjustment, cutoff, and readiness details.

Status exposes node market_data (backend/status/xtquant_version), historical_data (READY/SYNCING/NOT_READY, native_through/fresh_through/target_through, sync progress, and supported/excluded scope with the exclusion-report path), trading (NOT_READY/TRADING_BACKEND_NOT_READY), and per-deployment worker_phase, market_data, control_authority, portfolio_sync, and trading. RUNNING indicates a real market Strategy runtime, not enabled trading. PAUSED remains durable RUNNING; a pause crossing a required event fails with MISSED_RUNTIME_EVENT. Companion restart fails orphaned RUNNING records with RUNTIME_LOST_ON_COMPANION_RESTART; no worker is resumed automatically.

Package installation and pinned API import checks do not establish MiniQMT connectivity. In the operating environment, verify the terminal connection, instrument metadata and trading periods, Shanghai/Shenzhen subscriptions, current quotes and exact-date daily queries. A staged Strategy should initialize in its child process, report READY/RUNNING with truthful status, and stop gracefully. To validate a complete trading session, observe BEFORE_TRADING, the 14:57 callback and rejected order, finalized daily data, AFTER_TRADING and SETTLEMENT using the real trading-day clock. The current trading backend remains unavailable; these checks do not enable order execution.
