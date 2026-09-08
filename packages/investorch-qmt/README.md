# InvestOrch QMT

`investorch-qmt` is the independently installable Windows companion MCP server for InvestOrch. It exposes an authenticated Streamable HTTP boundary that the Core application can use without importing either distribution into the other.

The B2 companion provides remote deployment staging, control leases and durable trade-fact delivery. It does not connect to QMT, inspect accounts, read positions, or place orders yet. A healthy service truthfully reports QMT as `not_connected`.

## Requirements

- Windows
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

Neither InvestOrch Core nor QMT/xtquant is required to install and run the companion.

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
- MCP `get_status` is read-only and returns `service.status = "ready"` with `qmt.status = "not_connected"` as a successful observation.

Operational logs rotate under `%LOCALAPPDATA%\InvestOrch\QMT\logs`. Authorization headers and bearer tokens are not logged.

These surfaces intentionally do not claim that QMT is installed, logged in, connected, or ready to trade. Real Big QMT connectivity is outside the current release.


## B2 remote execution

`serve` initializes `%LOCALAPPDATA%\InvestOrch\QMT\runtime.db` at companion schema v1. Newer schemas fail closed. Deployment metadata and a durable outbox are retained in SQLite with foreign keys, WAL and transactional writes. There is no Portfolio mirror. Core Portfolio schema remains v5 and Bootstrap remains V1.

The same listener serves `/mcp` and `/api/v1` with the same Bearer token and Host policy. Core derives the REST origin and headers from the referenced MCP profile; it does not need a second URL or token.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/node/status` | Node, control and deployment status without strategy source |
| POST | `/api/v1/control-sessions` | New node-wide in-memory session, fencing the old session |
| POST | `/api/v1/control-sessions/{session_id}/renew` | Renew control lease; optional reconciliation assertion |
| DELETE | `/api/v1/control-sessions/{session_id}` | Close current session; stale close cannot close a newer session |
| PUT | `/api/v1/deployments/{deployment_id}` | Validate and stage exact frozen deployment bytes |
| GET | `/api/v1/facts/next` | Deliver one globally oldest pending fact |
| POST | `/api/v1/facts/{fact_id}/ack` | Atomically acknowledge one fact and advance its deployment cursor |

Stage, pull and ACK require `X-InvestOrch-Control-Session`. Missing, expired or fenced authority returns `409 STALE_CONTROL_SESSION`. Sessions expire after the returned `lease_timeout_seconds`; they never survive process restart. Expiry invalidates sync and does not kill a runtime. Core maintains heartbeats only while it has ACTIVE live work.

A lease renewal without a body renews authority only. After draining and comparing the Core canonical head, Core may send:

```json
{"reconciled_deployments":[{"deployment_id":"deployment-a","acked_core_sequence":12}]}
```

The service accepts this assertion only when the deployment has no pending facts and the cursor matches. Session replacement/expiry clears synchronization; a mismatch is DESYNCED. Ordinary heartbeat cannot restore SYNCED. This handshake does not perform broker reconciliation or adopt an unknown deployment.

Staging validates the exact manifest field set, RQAlpha 6.3.0, Base64-decoded bytes against SHA-256, and independent Bootstrap V1 identities before installing `deployments/<deployment_id>/{strategy.py,manifest.json,bootstrap.json}`. Repeating the identical frozen deployment returns the same summary; changed content or another current deployment for the Portfolio conflicts. A failed database write removes newly installed artifacts. Persisted artifacts are not overwritten to repair corruption automatically.

The internal `ExecutionNodeService.enqueue_trade_fact(payload)` seam accepts strict TRADE_V1 facts for a future real broker callback. There is no enqueue REST endpoint or Agent tool. Numeric fields are finite decimal strings, timestamps include a timezone, and broker trade identity deduplicates exact payloads. The outbox returns one fact, retains ACKED rows, and requires an oldest-only ACK with Core sequence exactly N+1. An identical ACK retry succeeds; a changed sequence conflicts. ACK and the deployment cursor commit in one SQLite transaction.

MCP exposes only `get_status`, `start_live_strategy(portfolio_id)` and `stop_live_strategy(portfolio_id)`. Configure approval for start and stop in Core. Start requires a STAGED deployment, control authority and reconciled sync, then truthfully returns `BACKEND_NOT_READY` without changing STAGED. Stop changes STAGED to STOPPED and retries idempotently; FAILED remains FAILED and a RUNNING stop cannot fabricate success. Core releases ACTIVE ownership only after observing the terminal state, draining and reconciling.

B2 does not include a fake broker/event source, production RQAlpha loop, actual QMT order placement, broker reconciliation, WebSocket transport or TLS/PKI. QMT remains `not_connected`. Do not use this plaintext service across an untrusted LAN or public Internet.
