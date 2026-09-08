# QMT remote execution protocol

The remote execution protocol transfers a frozen Core deployment to one Windows execution node and maintains control authority and reliable TRADE_V1 delivery. An approved `start_live_strategy` with current control authority starts a spawned RQAlpha `LIVE_TRADING` worker connected to MiniQMT/xtdata. The trading backend remains unavailable; orders reaching the broker receive `TRADING_BACKEND_NOT_READY` without fills.

## Connection configuration

Core `investorch.toml` selects a server from its existing `mcp.toml`:

```toml
[qmt]
mcp_server = "qmt"
```

Configure that server in `mcp.toml`:

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

The token uses the existing MCP secret expansion mechanism. REST reuses the expanded headers and server timeout, falling back to `mcp.default_timeout_seconds`. Do not configure a second REST URL or token. Core parses the MCP URL to derive `http://192.168.1.20:8765/api/v1`; the supported MCP path is `/mcp` or `/mcp/`, without URL credentials, query, or fragment.

An empty `qmt.mcp_server` is valid and leaves research/backtesting available. A reference to an unknown or disabled server is a static configuration error. The selected QMT server must list both `start_live_strategy` and `stop_live_strategy` in `require_approval`; missing or partial approval configuration fails before connecting. Approval lists on other MCP servers remain independently configurable. Changing this setting requires a Core restart. An unconfigured deployment returns `EXECUTION_NODE_NOT_CONFIGURED` before creating a deployment or frozen files. An offline configured Windows node does not require Core startup to fail, and no ACTIVE deployment means no recurring node reconnect or heartbeat.

## Tools and ownership

| Surface | Tool | Approval |
| --- | --- | --- |
| Core | `list_broker_accounts()` | No |
| Core | `deploy_live_strategy(portfolio_id, broker_account_id)` | Yes |
| Core | `get_live_status(portfolio_id=None)` | No |
| QMT MCP | `get_status()` | No |
| QMT MCP | `start_live_strategy(portfolio_id)` | Yes, through MCP configuration above |
| QMT MCP | `stop_live_strategy(portfolio_id)` | Yes, through MCP configuration above |

Deployment IDs are internal identities; the Agent does not choose them or call heartbeat, staging, outbox, or ACK tools. Deployment approval uses the ordinary approval flow. Only after approval does Core read the current StrategyBinding and freeze source bytes and parameters.

Core owns the canonical Ledger and LiveDeployment reservation. The companion ExecutionNodeService is the single business entry point behind both adapters; it owns remote metadata, staged files, control availability, and pending facts. The two independently installed distributions exchange wire data and never import each other or a shared DTO package. Both retain RQAlpha 6.3.0.

## REST surface

All routes share `/mcp`'s server, port, Bearer authentication, and Host/Origin protection.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/node/status` | Observe node/control state and retained deployment summaries |
| POST | `/api/v1/control-sessions` | Acquire the exclusive node-wide lease when free or expired |
| POST | `/api/v1/control-sessions/{session_id}/renew` | Renew the current lease; optionally assert reconciliation |
| DELETE | `/api/v1/control-sessions/{session_id}` | Close that session without invalidating a newer one |
| PUT | `/api/v1/deployments/{deployment_id}` | Validate and stage an immutable deployment |
| GET | `/api/v1/facts/next` | Return one oldest PENDING fact, or `{"fact": null}` |
| POST | `/api/v1/facts/{fact_id}/ack` | Acknowledge one committed canonical sequence |

Stage, next-fact, and ACK require `X-InvestOrch-Control-Session`. Missing, expired, or superseded authority produces `409 STALE_CONTROL_SESSION`. Sessions live only in Windows process memory and become invalid on restart. Lease responses provide `lease_timeout_seconds`; Core renews at one third of that interval while ACTIVE work exists. Expiry fails control closed without killing a runtime or changing Core ownership.

Opening a session while a current lease is valid returns HTTP 409 with `{"code":"CONTROL_SESSION_BUSY","message":"Another Core currently owns the execution-node control lease.","retryable":true}`. A successor can acquire control only after expiry or explicit close; a stale close cannot invalidate the successor. Core keeps an existing ACTIVE deployment and reports UNKNOWN with the busy reason. In `get_live_status`, node `availability` reports reachability and `control_authority` reports this Core's authority independently. A busy new preflight ends only its PREPARED deployment as FAILED.

MCP `get_status` requires Bearer authentication only. MCP start and stop also require the current `X-InvestOrch-Control-Session` header, checked before deployment state or backend readiness. The Core SDK HTTP auth provider reads current coordinator authority for each request, removing the header when authority is cleared. The companion reads it from the SDK's request-local context. The Agent still supplies only `portfolio_id`; authority does not replace approval. Idle MCP connects without acquiring a lease.

Ordinary renewal has an empty body. After draining and checking canonical heads, Core can send:

```json
{"reconciled_deployments": [{"deployment_id": "deployment-123", "acked_core_sequence": 13}]}
```

The companion requires each asserted deployment to have no pending facts and the matching ACK cursor before restoring its synchronization state. Renewal alone does not reconcile broker orders, fills, positions, or cash.

Errors use `{"code": "...", "message": "...", "retryable": false}`. Timeout/reset and malformed responses do not establish whether a write committed. The REST client makes one request per operation; retry decisions belong to the coordinator.

## Frozen staging and retry

For a new deployment, Core prepares the private artifact, checks node reachability, activates ownership, persists Bootstrap V1, and sends a PUT containing `manifest`, `strategy_source_base64`, and `bootstrap`. Windows decodes exact bytes, verifies the SHA-256 and RQAlpha version, cross-checks deployment/Portfolio/account identity, validates Bootstrap, and atomically installs files plus metadata.

Identical content under the same deployment ID is idempotent. Conflicting content or another current deployment for the Portfolio is a conflict. A preflight failure marks PREPARED as FAILED without an ACTIVE lock. An explicit initial validation rejection can release ownership as FAILED. A PUT timeout leaves Core ACTIVE and reports UNKNOWN with a safe same-deployment retry.

An existing ACTIVE retry first checks BrokerAccount identity, then reuses the frozen deployment. Workspace edits are ignored. The original persisted Bootstrap, including its timestamp, is reused after restart. An absent remote deployment is re-staged with those same inputs; missing or inconsistent original inputs fail closed rather than manufacturing a new snapshot.

## TRADE_V1 and reliable delivery

```json
{
  "schema_version": 1,
  "deployment_id": "deployment-123",
  "broker_trade_id": "broker-trade-123",
  "instrument": {"code": "600519", "market": "XSHG"},
  "side": "BUY",
  "quantity": "100",
  "price": "10.50",
  "commission": "1.00",
  "tax": "0",
  "other_fee": "0",
  "effective_at": "2026-09-08T01:23:45+08:00"
}
```

The field set is exact. Version is integer 1; identifiers are nonempty trimmed strings; timestamps include a timezone. Amounts are finite decimal strings, quantity/price are positive, and fees are nonnegative. Core and companion validate independently. The companion provides internal enqueue for tests and future broker callbacks, without a public enqueue route or production fake broker.

The durable outbox uses node-wide FIFO. Core pulls one oldest PENDING fact, invokes canonical idempotent live ingestion, checks that the returned Ledger sequence is the deployment's remote ACK cursor plus one, and ACKs before pulling again. ACK atomically records the committed sequence and advances that deployment's cursor. Repeating the same ACK is idempotent; out-of-order ACK or a sequence jump conflicts. Lost responses are recovered through redelivery and canonical idempotency, without duplicate trades.

Empty outbox plus equal canonical head and remote ACK cursor permits SYNCED. Pending delivery is COMMIT_PENDING; an unexplained sequence advance, identity conflict, or invalid economic fact fails synchronization closed. The pending fact is retained; the coordinator does not repair broker state or jump cursors.

## Recovery and terminal history

ApplicationHost owns the coordinator. ACTIVE work triggers recovery at startup; failures back off through 1, 2, 5, 10, and then 30 seconds. Foreground actions can retry immediately. Heartbeat failure drops local authority and triggers recovery. After an ambiguous transport failure, Core keeps the old session identity private and attempts renewal before restoring authority; a confirmed stale session permits a new acquisition. This prevents retries from competing with their own still-valid exclusive lease. Recovery drains and reconciles before declaring synchronization restored; the final ACTIVE deployment ending stops recurring network work.

A QMT MCP connection that failed at startup is retried on foreground deployment/status, explicit connection recovery, or existing ACTIVE recovery. The selected QMT server has its own SDK manager so retries do not change generic MCP behavior. There is no idle MCP retry worker. MCP refresh happens outside REST reconciliation, and a failed refresh does not undo successful staging. Each Agent Run takes one fresh active-server snapshot; reconnect affects subsequent Runs without changing the tools of an in-flight Run. This recovery addresses failed connection attempts; it does not add general transport-health polling for previously established connections.

With current control authority, STAGED stop becomes STOPPED; repeated stop is idempotent. FAILED remains terminal. Stopping a RUNNING worker closes its safety gate, requests graceful teardown, and terminates it after a bounded timeout if needed. Core transitions to STOPPED/FAILED only after the matching remote terminal state, drained facts, and equal sequences. It never adopts another remote deployment or reactivates terminal Core ownership.

The companion retains terminal deployment metadata, artifacts, and ACKED facts. Historical terminal rows with no pending work coexist with a successor; they must not be mistaken for the Portfolio's current deployment. Current remote work without matching ACTIVE Core ownership, or terminal Core ownership with remote RUNNING, is DESYNCED.

Companion `%LOCALAPPDATA%\InvestOrch\QMT\runtime.db` uses schema version 1 with `remote_deployments` and `outbox`, no Portfolio mirror. Core Portfolio schema remains v5 and Bootstrap remains V1. There is no retention worker.

## Network and capability boundary

Supported deployment is localhost, a trusted LAN, or a private VPN. Bearer authentication is combined with the trusted network boundary and `allowed_hosts` Host/DNS-rebinding protection; `allowed_hosts` is not a source-IP ACL. The service does not support TLS/PKI, OAuth, mTLS, or WebSocket, and plaintext service is not intended for a hostile network or public Internet. Tokens and strategy source/Base64 must not enter ordinary logs.

Market connectivity and trading availability are separate: real xtdata market events are supported, while broker submission/callbacks and broker reconciliation remain unavailable. See the [runtime model](RQAlpha_Live_Runtime_Model.md) and [companion setup](../packages/investorch-qmt/README.md).

## Worker startup and truthful status

Start requires STAGED, SYNCED, and current authority. The child revalidates staged artifacts, bootstraps the native Portfolio, initializes the Strategy and runtime, connects xtdata, subscribes, checks bundle/reference readiness, and verifies session eligibility before sending READY. Only READY permits durable RUNNING. A PID alone is not evidence of readiness.

Non-trading-day and pre-session starts are allowed. A first start after the session begins returns retryable `SESSION_ALREADY_STARTED` and stays STAGED. `MARKET_DATA_NOT_READY` and `START_TIMEOUT` also keep STAGED after cleaning up the child. Invalid artifacts, Bootstrap, instruments, or Strategy initialization fail the deployment. Concurrent starts share startup; stop can cancel STARTING; start during STOPPING is rejected. STOPPED and FAILED require a new deployment.

Node status exposes `market_data` (`backend`, `status`, `xtquant_version`), `trading` (`status`, `reason`), and `control`. Market health describes actual worker evidence, not service readiness. Per-deployment status adds `worker_phase`, `market_data`, `control_authority`, and `trading` alongside durable `status` and `portfolio_sync`. Core `get_live_status` aggregates these with ownership; its `node.remote_status` is the remote durable state. A typical worker may be remote RUNNING, phase PAUSED, market CONNECTED, control UNAVAILABLE, and trading NOT_READY at the same time. Core clears ephemeral health to unknown on a node outage.

Authority or synchronization loss pauses event delivery. Crossing an unprocessed required daily boundary while paused fails with `MISSED_RUNTIME_EVENT`; no catch-up is attempted. Market disconnection or an unexpected worker exit fails the runtime. Companion restart changes orphaned durable RUNNING rows to FAILED with `RUNTIME_LOST_ON_COMPANION_RESTART`; workers are not automatically restarted.

The parent alone writes runtime.db. Worker phase and market health are process-local, with no persisted PID or schema extension. Production execution produces no TRADE_V1 facts, so the outbox ordinarily remains empty. Core schema v5, Bootstrap V1, runtime.db v1, and TRADE_V1 remain unchanged.
