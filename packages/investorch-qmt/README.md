# InvestOrch QMT

`investorch-qmt` is the independently installable Windows companion MCP server for InvestOrch. It exposes an authenticated Streamable HTTP boundary that the Core application can use without importing either distribution into the other.

Version 0.1.0 provides the initial companion service foundation. It does not connect to QMT, inspect accounts, read positions, or place orders yet. A healthy service truthfully reports QMT as `not_connected`.

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

Wildcards are rejected. LAN mode is intended only for a trusted local network or private VPN. Do not expose the HTTP endpoint directly to the public Internet; it does not provision TLS, OAuth, or mTLS.

## Configure InvestOrch Core

On the Core machine, add the companion to the existing `mcp.toml` registry:

```toml
[[servers]]
name = "qmt"
enabled = true
transport = "streamable_http"
url = "http://192.168.1.20:8765/mcp"
cache_tools_list = false

[servers.headers]
Authorization = "Bearer ${QMT_MCP_TOKEN}"
```

Store the value printed by `investorch-qmt token show` in the Core's existing `investorch.toml` secret section:

```toml
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
- The currently available MCP Tool, `get_status`, is read-only and returns `service.status = "ready"` with `qmt.status = "not_connected"` as a successful observation.

Operational logs rotate under `%LOCALAPPDATA%\InvestOrch\QMT\logs`. Authorization headers and bearer tokens are not logged.

These surfaces intentionally do not claim that QMT is installed, logged in, connected, or ready to trade. Real Big QMT connectivity is outside the current release.
