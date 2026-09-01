# InvestOrch Agent Architecture

[简体中文](InvestOrch_Agent_Architecture.zh-CN.md)

## Scope

This document describes the architecture implemented in 0.1.0. Future product directions are recorded in the [Product Roadmap](InvestOrch_Agent_Product_Roadmap.md).

## System context

```text
                   User
              /             \
          Web client       Textual TUI
              \             /
               Application Host
                      |
              Application services
                      |
                 AgentRuntime
                      |
              OpenAI Agents SDK
                      |
             Main and support agents
                /             \
          built-in Tools       MCP servers
                |
       workspace / RQAlpha / local state
```

The plain console reaches the same application host as a sequential diagnostic interface.

## Composition and ownership

`application.host.open_application_host()` is the composition boundary shared by Web, TUI, and plain modes. It creates and closes:

- validated `AppConfig`;
- `AppState` future-run defaults and selected-session state;
- application-global `ExecutionState`;
- `SessionJournal` and session operations;
- OpenAI Responses models and Agent definitions;
- MCP server manager;
- approval, activity-label, presentation, and runtime coordinators.

The host owns process-lifetime resources. A Run never owns the workspace, MCP manager, or managed background processes.

## Presentation layer

### Web

`investorch web` runs a FastAPI application on `127.0.0.1`. The server provides REST operations for session and interaction state, paged journal history, defaults, queue, compaction, and approvals. A WebSocket transports live application events. The React frontend consumes these application contracts and uses the Web configuration returned by bootstrap as its source of UI defaults.

### TUI

`investorch` starts the Textual client. It uses the same application services and Runtime callbacks as Web. Session selection, timeline projection, composer behavior, Queue, Todo, approval, processes, and usage are presentation concerns; they do not own execution state.

### Plain console

`investorch --plain` runs a sequential diagnostic client. It renders raw output and uses inline approval. It does not offer the concurrent interaction surface of Web or TUI and does not run the Activity Agent.

`presentation.py` provides the transport-neutral JSON-safe projections shared by live Web events and persisted journal history.

## Session, Run, and interaction model

The application separates three identities:

- **Session**: persistent conversation identity.
- **Run**: one transient top-level Agent turn.
- **Selection**: the Session currently displayed by a client.

`AgentRuntime` owns active Runs and follow-up queues. It permits at most one top-level Run per Session and permits Runs in different Sessions to execute concurrently. Each Run captures immutable reasoning-effort, permission-mode, and follow-up settings.

Steer continues the current top-level Run at a safe turn boundary. Queue records future intent and promotes it to a new Run after successful completion. Stop cancels the selected Session's active Run and pauses retained Queue intent.

Detailed invariants are defined in the [Runtime / Session Execution Model](Runtime_Session_Execution_Model.md).

## Agent integration

InvestOrch uses OpenAI Agents SDK rather than implementing an Agent loop or Tool-call protocol. `AgentLoop` adds application behavior around SDK runs: streaming output, approval continuation, title generation, usage, compaction, and Steer continuation.

The Main Agent is cloned with captured model settings for each Run. Support agents have narrow responsibilities:

- Title Agent generates a Session title.
- Activity Agent creates presentation-only labels for Tool calls.
- Permission Agent may return approve, reject, or ask in review mode.
- Compact Agent replaces SDK continuation history with a marked summary.
- Bootstrap Agent merges project templates during `investorch --sync`.

The runtime uses the OpenAI Responses model adapter. Bundled configuration currently points all roles to DeepSeek. Model name, base URL, secret name, and reasoning effort come from `AppConfig`.

## Current Tool surface

The Main Agent currently receives:

- workspace and execution: `explore`, `edit`, `delete`, `exec_command`;
- utility and state: `calculate`, `get_current_time`, `write_todos`;
- configuration: `get_config`, `update_config`;
- MCP registry: `list_mcp_servers`, `configure_mcp_server`, `remove_mcp_server`;
- backtesting: `run_backtest`, and `inspect_rqalpha_data` when the native bundle is selected.

Tool implementation uses Agents SDK Tool definitions directly. There is no local Tool framework, registry abstraction, Market Tool, Portfolio Tool, Trading Tool, or QMT module in 0.1.0.

Workspace-changing and execution capabilities enforce workspace boundaries and approval policy. Tool failures surface as explicit exceptions.

## Approval boundary

Approval is coordinated at the application boundary. Every request has an immutable approval ID plus Session and Run ownership. Permission modes are:

- `manual`: always ask the user;
- `review`: use the Permission Agent when it can decide safely, otherwise ask.

Current approval protects configured consequential Tools, including arbitrary workspace command execution and backtesting of ordinary Python strategy files. Approval authorizes execution; it is not a Python sandbox.

## Workspace and background execution

`ExecutionState` is application-global. It contains the shared workspace sandbox and managed background jobs. A job may outlive the Run that created it and retains owner Session/Run attribution. Session selection and Run completion do not stop jobs.

All Sessions share the same workspace. Concurrent Runs can therefore target the same file; 0.1.0 has no per-Session workspace or filesystem lock.

## Backtesting

`run_backtest` validates a Workspace-relative RQAlpha strategy, captures one immutable configuration snapshot, executes a daily stock backtest, and writes reproducibility metadata plus analyser artifacts under the configured Workspace directory.

The default data path is a native RQAlpha bundle. When `backtest.use_cnequity=true` and the optional dependency is installed, RQAlpha loads `investorch.backtest.rqalpha_mod` by configuration string and overlays CNEquity daily bars and adjustment factors while retaining RQAlpha market semantics.

CNEquity is optional and user-operated. `investorch data` passes arguments to its CLI, and the application may compose its read-only stdio MCP server. CNEquity itself owns ingestion, repair, retry, locking, and recovery.

## Persistence

Persistent state under the configured root is intentionally split by responsibility:

- `<root>/investorch.toml`: local overrides and secrets;
- `<root>/mcp.toml`: MCP registry;
- `<root>/workspace/`: user-owned workspace and generated artifacts;
- `<state>/sessions.db`: Agents SDK continuation plus application session metadata;
- `<state>/sessions/<session-id>.jsonl`: append-only user-visible journal;
- `<state>/logs/investorch.log`: rotating diagnostic log.

The SQLite continuation is model state and may be compacted. The JSONL journal is replay state and is not compacted. Activity labels are derived annotations, not execution truth.

Bootstrap templates are copied only when their target is absent. `--sync` uses the configured Bootstrap Agent to merge current project rules into existing user-owned files while preserving durable content. `--sync-force` skips the model and atomically replaces targets with the bundled templates. Both modes validate every changed target, restore the current target if its operation fails, and retain backups for replaced content under `<state>/bootstrap-backups/<timestamp>/`.

## Configuration

`AppConfig` validates bundled TOML defaults plus local overrides. Some settings are hot for future Runs; composition-changing settings report that restart is required. Agent-facing reads redact secrets, and Agent-facing writes cannot modify secrets.

The configured model and MCP endpoints are external trust boundaries. Local-first means state and workspace ownership are local by default; it does not mean model or MCP traffic stays on the machine.

## Dependency direction

```text
Web / TUI / plain
        -> application services
        -> Runtime and presentation contracts
        -> Agent integration and Tools
        -> workspace, storage, RQAlpha, MCP, model endpoints
```

Run and persistence ownership stays in the application and Runtime layers. Client command parsing stays in presentation. Backtest code remains independent of UI and Session selection.

## Current limits

0.1.0 does not implement:

- portfolio, account, order, position-monitoring, or live-trading capabilities;
- a QMT gateway or direct XtQuant integration;
- a unified investment data layer;
- Multi-Agent orchestration;
- persistence of active Runs, pending approvals, Steer entries, Queue state, or Todo state across restarts;
- historical-turn conversation branching;
- per-Session workspaces or cross-Run filesystem locking;
- authenticated LAN or remote Web serving.
