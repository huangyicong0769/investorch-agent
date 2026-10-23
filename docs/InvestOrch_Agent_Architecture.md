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

`AgentRuntime` owns active Runs and follow-up queues. It permits at most one top-level Run per Session and permits Runs in different Sessions to execute concurrently. Each Run captures an immutable `AgentModelConfig` (connection, actual model, effort and window), permission mode and follow-up behavior. Session Drafts are Host-owned; Queue resolves the latest Draft only when promoted, while its submitted permission/follow-up defaults remain frozen.

Steer continues the current top-level Run at a safe turn boundary. Queue records future intent and promotes it to a new Run after successful completion. Stop cancels the selected Session's active Run and pauses retained Queue intent.

Detailed invariants are defined in the [Runtime / Session Execution Model](Runtime_Session_Execution_Model.md).

## Agent integration

InvestOrch uses OpenAI Agents SDK rather than implementing an Agent loop or Tool-call protocol. `AgentLoop` adds application behavior around SDK runs: streaming output, approval continuation, title generation, usage, compaction, and Steer continuation.

The Main Agent is cloned with captured model settings for each Run. Support agents have narrow responsibilities:

- Title Agent generates a Session title.
- Activity Agent creates presentation-only labels for Tool calls.
- Permission Agent may return approve, reject, or ask in review mode.
- Compact Agent replaces SDK continuation history with a marked summary.

The runtime retains OpenAI Agents SDK and its Responses adapter. `models.<id>` defines reusable connections; `agents.<role>` selects a connection, actual model, effort and positive window for the five built-in roles. `AppConfig.model_connection()` and `agent_model()` resolve these separate values. Skill Review shares `agents.permission`. The Host owns `ModelConnectionClients`, reusing clients by connection and closing them on shutdown; each Run clones its model/settings without mutating the shared Agent template.

ChatGPT connections use verified local OAuth authorization and the official direct Responses endpoint. A thin adapter sends streaming, non-stored requests, restricts unsupported fields and collects non-streaming SDK callers through the stream. The Host owns `ChatGPTAuth`; the Web Setup server can authorize before the Agent Host exists. Credentials refresh on demand under a credential-specific lock and rotate atomically in the same private TOML as Settings. Public configuration contains no tokens. See [Configuration](Configuration.md).

## Current Tool surface

The Main Agent currently receives:

- workspace and execution: `explore`, `edit`, `delete`, `exec_command`;
- utility and state: `calculate`, `get_current_time`, `write_todos`;
- Session discovery and history: `list_sessions`, `read_session`;
- Session input submission: `send_message` (sender approval required);
- configuration: `get_config`, `update_config`;
- MCP registry: `list_mcp_servers`, `configure_mcp_server`, `remove_mcp_server`;
- backtesting: `run_backtest`, and `inspect_rqalpha_data` when the native bundle is selected.

Tool implementation uses Agents SDK Tool definitions directly. Portfolio tools delegate to PortfolioOperations; Skill tools delegate to SkillOperations; Session read tools delegate to stateless Application queries in `session_queries.py`. These adapters do not write persistence directly.

Workspace-changing and execution capabilities enforce workspace boundaries and approval policy. Tool failures surface as explicit exceptions.

## Session access (0.3.0 Stage A)

Read-only Session access follows this boundary:

```text
Agent Session Tools (Host-bound queries)
├── Application list_sessions -> Storage metadata + Runtime.session_snapshot(target_id)
└── Application read_session  -> Storage existence + existing SessionJournal + shared Steer facts
```

`application.session_queries.list_sessions` and `read_session` are ordinary async functions with explicit dependencies and no hidden read position. The Host constructs `Agent -> AgentLoop -> AgentRuntime`, then binds queries with `functools.partial` and appends `make_session_tools(...)` to the existing Agent once, before yielding the Host. Per-Run Agent clones see that complete Tool list without registration during Runs. Each Host binds the same live AppConfig along with its own database, Runtime, and existing Journal; the history query requires no Runtime. Each query snapshots AppConfig at call entry so its default, validation and output budgets remain consistent for that call. Tools only forward validated public arguments to these bound queries. Shared Journal facts resolve Steer activation/discard for both history and Review, while Review retains its strict instruction-boundary policy. No additional history database or Runtime state machine is introduced.

`list_sessions(query=None, include_archived=False, limit=None, offset=0)` lists metadata in the current Root. A blank query leaves titles unfiltered; a nonblank query uses Unicode `casefold()` substring matching on title only. There is no Session-ID or message-content filter. Archived Sessions are excluded by default; `include_archived=True` includes both archived and unarchived Sessions. Rows expose `session_id`, `title`, `archived`, `branch_from_session_id`, `created_at`, `updated_at`, and `status`, ordered by descending `updated_at` with `session_id` breaking ties. Continue with `next_offset` while `has_more` is true. Offset pages reflect the current listing and are best effort during concurrent changes, without a frozen list snapshot.

List status is `running`, `waiting_approval`, or `stopping` for an active Run. Without an active Run it is `pending` when accepted Queue or Steer work remains, otherwise `idle`. Queue counts, pause flags and pending Steer details are not returned. Archive is separate metadata; historical failure or a previous process's active Run does not determine current status. Listing reads metadata and current Runtime snapshots, without scanning Session journals.

`read_session(session_id, view="conversation", before_seq=None, snapshot_seq=None, limit=None)` requires an existing exact Session ID in the current Root, including self, another running Session, or an archived Session. It returns history and paging fields, without Session metadata, current status, or a last-Run outcome. Reads do not change Selection, start a Run or mutate a Session.

The default `conversation` view returns flat `user`, `assistant`, `tool_call`, and `tool_result` records with original Journal `seq` and `timestamp`. Only activated Steers appear, as ordinary `user` records at their original input sequence. Pending/discarded Steers, reasoning, approvals and maintenance events are excluded. A tool result remains a standalone observation even when its call is on another page; no Tool-call ID is fabricated. Images expose sanitized `filename`, `media_type`, `detail` and `image_count` metadata, without URLs or image bytes. Optional `events` preserves validated raw Journal fields and values, including reasoning, approvals and image payloads, subject to the response budget.

History pages return the newest eligible records in ascending source sequence. `limit` counts visible records in Conversation and raw records in Events. The first read captures a committed Journal head as `snapshot_seq`; older pages reuse that value and pass `next_before_seq` as the exclusive `before_seq` cursor. Later appends are outside the frozen history. Activation/discard proof is examined through the entire snapshot even when it follows `before_seq`: a Steer at `seq=10`, activated at `seq=18`, remains visible for `before_seq=15, snapshot_seq=18`. Activation after the snapshot can appear only in a fresh read.

For example, `read_session(session_id="session-b", limit=2)` can return:

```json
{
  "session_id": "session-b",
  "view": "conversation",
  "snapshot_seq": 73,
  "records": [
    {"seq": 70, "timestamp": "2026-10-13T03:00:00+00:00", "type": "tool_result", "output": "Baseline complete."},
    {"seq": 72, "timestamp": "2026-10-13T03:01:00+00:00", "type": "assistant", "text": "The baseline result is ready."}
  ],
  "has_older": true,
  "next_before_seq": 70,
  "omitted_fields": 0
}
```

Continue with `read_session(session_id="session-b", before_seq=70, snapshot_seq=73, limit=2)`. That page can return `seq=67,69`; appended `seq>73` remains excluded. An existing Session with an absent/empty Journal returns `snapshot_seq=0`, empty records, `has_older=false`, and `next_before_seq=null`.

Both Tools inherit omitted or null limits from hot AppConfig `session_tools.default_page_size`; explicit limits range from 1 through `session_tools.max_page_size`. Their defaults are 200 and 500 records. AppConfig `session_tools.max_response_chars` bounds the serialized response, including escaping (default 262,144 characters). Read current values with `get_config()`. A Conversation page can become shorter to retain complete records; it keeps the newest consecutive eligible suffix so the older-page cursor recovers the remainder. A single `text`, `arguments`, or `output` field above hot AppConfig `session_tools.max_field_chars` (default 131,072 original characters), or one whose encoded record exceeds the response budget, is explicitly omitted without clipping. The record retains identity/metadata and an `omitted_fields` entry such as `{"field":"output","reason":"size_limit","original_chars":280000}`; the response-level `omitted_fields` counts these entries. The omission row advances pagination. Metadata that cannot fit raises `response_too_large`.

Raw Events are never rewritten to fit: an oversized requested page raises `response_too_large` with guidance to reduce `limit` or use Conversation. A single oversized raw event cannot be retrieved through Events under this budget. Invalid arguments/cursors, missing IDs, malformed committed history, conflicting Steer evidence and unavailable journals return explicit safe errors; absence of a Journal for a valid Session is empty history. Snapshot capture uses a short Journal lock and scans the committed prefix without reserving Runtime maintenance or holding the write lock for the historical scan. Stage A adds no cross-Session messaging or spawn capability.

## Session messaging

The Host registers `send_message` once, bound to its AppState, Runtime, and existing Journal. The SDK interrupts the **sender's Run** for Permission/Review/HITL before invoking `application.messaging.send_message()`. The Application requires an exact existing, unarchived target in this Root, rejects self-send, prepares ordinary `UserInput`, and calls `submit_user_input()`. Approval never grants the recipient's later tools permission.

`send_message(target_session_id, text="", images=None)` accepts unchanged text and compact image references. Text may be empty when an image is present. References have `source`, nullable `path`, `seq`, `image_index`, and `detail`:

- `workspace`: set a Workspace-relative `path`. Shared containment rejects absolute paths and traversal/symlinks outside Workspace; only regular files are read, at most `images.max_image_bytes + 1` bytes. MIME comes from bytes, not the extension.
- `session_event`: set positive Journal `seq` and zero-based `image_index` in the **sender's own** committed history. User, activated Steer, assistant, and tool-output images are supported; pending/discarded Steers cannot supply images. There is no source-Session argument.
- Other source fields are null/omitted. Optional `detail` overrides the existing image detail. Both sources become data URLs inside the Application and use the existing `normalize_user_input()` validation and configured image limits; models never supply Base64 or download arbitrary URLs.

For example, discover a complete target ID with `list_sessions`, then call `send_message(target_session_id="<target-id>", text="Please inspect this chart", images=[{"source":"workspace","path":"charts/result.png"}])`. To forward an uploaded image without text, use `send_message(target_session_id="<target-id>", images=[{"source":"session_event","seq":1,"image_index":0}])`. Use `read_session` for later results, or explicitly send a reply to the trusted sender ID.

Each immutable `UserInput` carries an immutable `InputOrigin`. External inputs default to `user`; the Tool derives Agent Session/Run IDs from SDK context. Agent origin is optional metadata on existing `user_message`/`user_steer` events, with the target Run ID unchanged. SDK input stays `role=user` and adds a separate application-generated provenance text item before the original text/images. Review includes the source on effective instructions under its existing frozen boundary and Steer activation policy. Conversation reads retain `type=user`, source metadata and image metadata; old history needs no migration.

Web/TUI reuse user-message components with a small Agent source label and full-ID navigation/copy or resume guidance. A generic Journal-committed input notification feeds existing history reconciliation and TUI sequence deduplication. Receipts confirm only Run/Steer/Queue acceptance; [Runtime](Runtime_Session_Execution_Model.md#agent-message-submission) defines their different persistence guarantees. Messaging adds no mailbox, task registry, completion callback, automatic reply, spawning, or cross-process transport. Concurrent Sessions still share Workspace and managed resources.

## Session spawning

The Host binds approval-required `spawn_agent(task, title?)` to ordinary Session creation and input submission. Children use the same Agent, Tools, Skills, Runtime and independent Permission rules. SQLite Spawn provenance is separate from Fork lineage; related Portfolio IDs are copied once without history, Ledger or authorization inheritance. Run completion and Stop are independent. A strict positive, hot `runtime.max_concurrent_runs` (default 3) applies to every top-level Run solely at `_start_run()`.

Delete and Clear share guarded recursive Spawn deletion, all-node Runtime reservations and one SQLite transaction. Active/pending nodes and external Fork references refuse deletion without cancellation; comparing confirmed Session IDs with the actual scope prevents unconfirmed expansion. Postcommit cleanup errors report committed identities accurately. Clear then creates an ordinary replacement. Web's compact right-hand context card uses read-only Storage/Runtime/Journal facts; TUI adds source metadata. See [Runtime](Runtime_Session_Execution_Model.md#session-spawning-and-global-capacity) for acceptance, recovery and deletion details.

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

AppConfig supplies execution defaults. The typed `parameters` argument overrides T+1, dividend and tax handling, price validation, matching, trading limits, slippage and transaction costs for one invocation. Null or omitted fields inherit defaults; explicit false and zero override them. The tool resolves one immutable effective parameter set without updating AppConfig, returns it as `runtime`, and records the same values in `request.json.runtime`. Initial cash and benchmark remain separate arguments. Source selection, paths and inspection limits remain application configuration; source selection also governs startup capabilities. Single-run assumptions use tool parameters; changing future application defaults uses the configuration workflow.

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

`AppConfig` exposes effective/saved public views, readiness, JSON Schema, atomic batch updates, reset-by-removing-override and independent snapshots. Complete candidate validation precedes writes; comments and neighboring settings are retained. Atomic file replacement and a small write lock protect persistence, and detected external edits reject stale writes. Restart-required changes remain next-start values. AppConfig contains no caller identity or authorization policy: the Agent Tool separately rejects edits to `models.*`, `agents.*`, `secrets.*`, `auth.*` and `permission.*`, while direct operator writes use the HTTP boundary.

All Local TOML is validated against the current Schema. Model provider fields must be configured explicitly; missing model settings or credentials enter Setup. Malformed TOML, unknown fields, and invalid values remain errors. Field descriptions and public views never include secret values.

`AppState` retains process-local Permission/follow-up overrides and references the shared Session model store. `/model` and `/effort` edit the selected Session Draft; the next actual Run commits its pure model values to Session metadata. The current Run remains frozen. Connections and Agent defaults require restart, while OAuth state applies immediately. SQLite model metadata contains no credentials; ordinary and spawned Sessions use the same contract.

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
- automatic orchestration, waiting, or completion notifications;
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
