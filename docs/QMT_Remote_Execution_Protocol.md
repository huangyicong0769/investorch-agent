# QMT remote execution protocol

The remote execution protocol transfers a frozen Core deployment to one Windows execution node and maintains control authority and reliable TRADE_V1 delivery. It does not connect QMT or run a production RQAlpha LIVE loop. `start_live_strategy` returns `BACKEND_NOT_READY` and leaves the deployment STAGED.

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

An empty `qmt.mcp_server` is valid and leaves research/backtesting available. A reference to an unknown or disabled server is a static configuration error. The selected QMT server must list both `start_live_strategy` and `stop_live_strategy` in `require_approval`; missing or partial approval configuration fails before connecting. Approval lists on other MCP servers remain independently configurable. Changing this setting requires a Core restart. An offline configured Windows node does not require Core startup to fail, and no ACTIVE deployment means no recurring node reconnect or heartbeat.

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
| POST | `/api/v1/control-sessions` | Open a new node-wide session, fencing the old session |
| POST | `/api/v1/control-sessions/{session_id}/renew` | Renew the current lease; optionally assert reconciliation |
| DELETE | `/api/v1/control-sessions/{session_id}` | Close that session without invalidating a newer one |
| PUT | `/api/v1/deployments/{deployment_id}` | Validate and stage an immutable deployment |
| GET | `/api/v1/facts/next` | Return one oldest PENDING fact, or `{"fact": null}` |
| POST | `/api/v1/facts/{fact_id}/ack` | Acknowledge one committed canonical sequence |

Stage, next-fact, and ACK require `X-InvestOrch-Control-Session`. Missing, expired, or fenced authority produces `409 STALE_CONTROL_SESSION`. Sessions live only in Windows process memory and become invalid on restart. Lease responses provide `lease_timeout_seconds`; Core renews at one third of that interval while ACTIVE work exists. Expiry fails control closed without killing a runtime or changing Core ownership.

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

ApplicationHost owns the coordinator. ACTIVE work triggers recovery at startup; failures back off through 1, 2, 5, 10, and then 30 seconds. Foreground actions can retry immediately. Heartbeat failure drops local authority and triggers recovery. Recovery drains and reconciles before declaring synchronization restored; the final ACTIVE deployment ending stops recurring network work.

STAGED stop becomes STOPPED; repeated stop is idempotent. FAILED remains terminal. Stopping a production RUNNING runtime is not supported and returns `BACKEND_NOT_READY`. Core transitions to STOPPED/FAILED only after the matching remote terminal state, drained facts, and equal sequences. It never adopts another remote deployment or reactivates terminal Core ownership.

The companion retains terminal deployment metadata, artifacts, and ACKED facts. Historical terminal rows with no pending work coexist with a successor; they must not be mistaken for the Portfolio's current deployment. Current remote work without matching ACTIVE Core ownership, or terminal Core ownership with remote RUNNING, is DESYNCED.

Companion `%LOCALAPPDATA%\InvestOrch\QMT\runtime.db` uses schema version 1 with `remote_deployments` and `outbox`, no Portfolio mirror. Core Portfolio schema remains v5 and Bootstrap remains V1. There is no retention worker.

## Network and capability boundary

Supported deployment is localhost, a trusted LAN, or a private VPN. Bearer authentication is combined with the trusted network boundary and `allowed_hosts` Host/DNS-rebinding protection; `allowed_hosts` is not a source-IP ACL. The service does not support TLS/PKI, OAuth, mTLS, or WebSocket, and plaintext service is not intended for a hostile network or public Internet. Tokens and strategy source/Base64 must not enter ordinary logs.

QMT status remains `not_connected`, and live backend capability remains unavailable. Real market events, broker submission/callbacks, and broker reconciliation are not yet implemented. See the [runtime model](RQAlpha_Live_Runtime_Model.md) and [companion setup](../packages/investorch-qmt/README.md).
