# InvestOrch Agent Architecture

[简体中文](InvestOrch_Agent_Architecture.zh-CN.md)

## Scope

This document describes the current source architecture. Future product directions are recorded in the [Product Roadmap](InvestOrch_Agent_Product_Roadmap.md).

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

Management routes add `/skills`, `/skills/:name`, `/settings`, `/system`, and `/setup`. Fresh configurations can serve Setup and read-only diagnosis before an Agent Host exists. Setup saves model fields and credentials through AppConfig, may test a model with an explicit small request, then starts the Host. Invalid existing configuration or incompatible storage enters recovery instead of being reinitialized.

`web.security` protects HTTP writes at the browser boundary: loopback Host, same-origin browser requests and JSON bodies. Non-browser local clients may omit Origin and Fetch Metadata. Management responses disable caching. This boundary is independent of Agent Tool approval and does not support authenticated remote serving.

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

The runtime uses the OpenAI Responses model adapter. The Schema defines five independently configurable model roles without a default provider. Model name, base URL, secret reference and reasoning effort come from `AppConfig`; readiness is checked before constructing an Agent. The independent Skill Review Agent reuses the Permission model configuration.

## Current Tool surface

The Main Agent currently receives:

- workspace and execution: `explore`, `edit`, `delete`, `exec_command`;
- utility and state: `calculate`, `get_current_time`, `write_todos`;
- configuration: `get_config`, `update_config`;
- MCP registry: `list_mcp_servers`, `configure_mcp_server`, `remove_mcp_server`;
- backtesting: `run_backtest`, and `inspect_rqalpha_data` when the native bundle is selected.

Tool implementation uses Agents SDK Tool definitions directly. Portfolio tools delegate to PortfolioOperations; Skill tools delegate to SkillOperations. Neither adapter writes persistence directly.

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
- `<state>/portfolio.db`: Portfolio schema v6;
- `<state>/skills.json`: Skill registration, enabled state and provenance;
- `<state>/sessions/<session-id>.jsonl`: append-only user-visible journal;
- `<state>/logs/investorch.log`: rotating diagnostic log.

The SQLite continuation is model state and may be compacted. The JSONL journal is replay state and is not compacted. Activity labels are derived annotations, not execution truth.


## Configuration

`config_schema.AppConfigSchema` is the single authority for field types, defaults, constraints, secret sensitivity and lifecycle (`hot`, `restart_required`, `immutable`). `AppConfig` combines Schema defaults, Local TOML overrides and permitted temporary configuration changes. No packaged default TOML participates at runtime. `paths.root` defaults to `~/.investorch`, accepts explicit programmatic bootstrap injection, and cannot be redirected by Local TOML.

`AppConfig` exposes effective/saved public views, readiness, JSON Schema, atomic batch updates, reset-by-removing-override and independent snapshots. Complete candidate validation precedes writes; comments and neighboring settings are retained. Atomic file replacement and a small write lock protect persistence, and detected external edits reject stale writes. Restart-required changes remain next-start values. AppConfig contains no caller identity or authorization policy: the Agent Tool separately rejects `secrets.*`, `permission.*` and `models.permission.*`, while direct operator writes use the HTTP boundary.

All Local TOML is validated against the current Schema. Model provider fields must be configured explicitly; missing model settings or credentials enter Setup. Malformed TOML, unknown fields, and invalid values remain errors. Field descriptions and public views never include secret values.

`AppState` owns optional process-local reasoning-effort, Permission and follow-up overrides. Unset options fall back to effective AppConfig; explicit overrides survive later configuration edits. `/defaults`, `/effort` and `/permission` do not persist configuration, and active Runs keep their captured `RunOptions`.

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

The current component does not implement:

- broker order execution, account mirroring, position-monitoring automation, or live trading;
- a QMT gateway or direct XtQuant integration;
- a unified investment data layer;
- Multi-Agent orchestration;
- persistence of active Runs, pending approvals, Steer entries, Queue state, or Todo state across restarts;
- historical-turn conversation branching;
- per-Session workspaces or cross-Run filesystem locking;
- authenticated LAN or remote Web serving.

## Skill Component

Core instructions retain identity, grounding, Memory, approval, and Skill loading rules. Specialized methodology lives in six bundled Skills. The package is only a distribution source: initialization materializes built-ins into `<workspace>/skills/<name>/`, the sole runtime content location. `<state>/skills.json` stores registration, enabled state, and provenance; metadata/version come from validated `SKILL.md`.

The application host freezes the enabled catalog and loadable names at startup. `load_skill` reads validated content/resources through an ordinary tool result retained in Session history. Supporting resources use `explore`; scripts use `exec_command` with existing approval. Lifecycle changes require restart, including newly opened sessions on the same host.

Non-built-in installation validates then independently reviews the current candidate using the Permission model configuration, with no reviewer tools. PASS proceeds through normal approval, WARN requires manual approval, and BLOCK cannot install. Dedicated mutation tools change durable behavior; installed built-ins cannot be removed or Agent-replaced. Fork to customize.

`investorch --update` explicitly replaces managed built-ins from the package and preserves enabled state without model calls or Portfolio initialization. Doctor/System Status compare installed and packaged content and report differences; differences are not permission to replace files automatically. Normal startup does not update content. Invalid/missing built-ins fail startup with update/reinstall guidance; invalid/missing custom Skills are excluded and remain inspectable. `MEMORY.md` is created only if absent; existing user files are retained.

## Management and recovery responsibilities

| Surface | Owner and behavior |
| --- | --- |
| Settings `/api/config` and Schema description | AppConfig validates and persists; frontend chooses layout from backend field semantics. |
| Setup `/api/setup` | Saves initial model/secret configuration before Host creation; an optional real request tests connectivity. |
| Skill catalog and detail | SkillOperations reads installed content without loading it into Agent context. |
| Skill Create / Install / Edit / Fork | SkillSessionWorkflows creates a Session with an explicit operation prompt; ordinary review and approvals remain required. |
| MCP `/api/integrations/mcp` | Shared `mcp.py` validation, registry persistence and secret-safe presentation; next-start changes, unknown runtime status unless observed. |
| System `/api/system`, `doctor`, `upgrade check` | Shared read-only diagnostics; no provider calls, MCP connection, initialization or migration. |
| Offline Backup Create / Verify | Snapshot and manifest verification; default archives omit raw credential configuration. |

Portfolio migration stays in the existing Portfolio migration module, including schema v6 and explicit retirement of legacy live state. The management layer reports required action instead of migrating. Restore is an operator procedure into a separate root while the application is stopped; there is no general Restore CLI or automatic rollback. See [Upgrade](Upgrade.md) and [Backup and Recovery](Backup_and_Recovery.md).

## Image conversation content

`ImageContent` and immutable `UserInput(text, images)` extend existing conversation contracts. Model input always uses structured Responses content. User raster data URLs are strictly decoded and validated against magic bytes and `[images]` limits; presentation additionally accepts SVG and HTTPS. `explore(read)` returns standard SDK text/image tool outputs, which remain images through output events, Journal, REST and WebSocket. The Web shares a gated HTTPS renderer with Markdown images, uses no-referrer and an image CSP, and never proxies remote content.

For charts in answer text, the Agent uses Markdown such as `![NAV curve](figures/nav.png)`. Workspace-relative image references render in place, while tool observations remain collapsed. The read-only `/api/workspace/image` endpoint serves JPEG, PNG, GIF and WebP within the Workspace and the configured per-image byte limit. These references display the current file, not a Session snapshot: overwriting or removing the file changes what an older message can display. Attached inline images retain their existing history persistence.

Main, Title and Compact use image-bearing SDK history. ReviewContext accepts image-only instruction markers; Activity uses text/count summaries. Permission does not infer authorization from an unseen image. Canonical durable copies are SDK SQLite history and JSONL Journal, without an asset subsystem.
