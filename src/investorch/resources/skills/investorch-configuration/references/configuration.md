# InvestOrch Agent Configuration System

This document describes the durable design and operating rules of the InvestOrch Agent configuration system.
It documents configuration semantics, not live configuration values.
Use `get_config` when the current effective value matters.
Never store secret values in this file.

## Configuration Layers

Effective application configuration is built from these layers, from lowest to highest priority:

1. Python `AppConfigSchema` defaults and constraints
2. Local instance configuration at `<root>/investorch.toml`
3. Runtime-only changes to the current `AppConfig`

Python Schema is the sole authority for types, defaults, constraints, sensitivity and `apply_mode`. There is no packaged default TOML. Local TOML stores overrides and credentials. `AppConfig` reads, validates, snapshots and writes configuration without knowing the caller identity.

A persistent runtime update is written to `<root>/investorch.toml` and therefore becomes part of the local instance configuration for later processes.

## Configuration Lifecycle

| lifecycle | keys | behavior |
| --- | --- | --- |
| immutable | `paths.root` | Cannot be changed through runtime configuration. |
| restart-required | `paths.workspace`, `paths.state`, `models.*`, `agents.*`, `secrets.*`, `permission.*`, `tui.*`, most `web.*`, `images.*`, `logging.*`, `mcp.*`, `cnequity.*`, `backtest.use_cnequity`, `backtest.rqalpha_bundle_dir`, `observability.sdk_tracing_enabled` | `persist=false` is rejected. `persist=true` writes the next-start value but does not change the current runtime; restart InvestOrch Agent to apply it. |
| hot | `runtime.*`, `activity.*`, `compaction.*`, `web.theme_mode`, execution/explore/calculate limits, `backtest.artifact_dir`, and backtest execution policy | Validated and applied to the current `AppConfig`; persistence is optional. The next applicable operation uses the new value. |

ChatGPT OAuth authorization records under `secrets.<credential>` are managed by the Host auth service; `auth.chatgpt_host_id` identifies this installation. Login, logout and refresh take effect without restart; Agents cannot edit those records.

`get_config` reports the value currently used by this process. A persisted restart-required update therefore continues to report the old value until the application is restarted.

`backtest.use_cnequity` controls the RQAlpha backtest overlay, the Agent's RQAlpha bundle inspection Tool surface, and whether the built-in CNEquity MCP server is started. Restarting applies all three changes together.

## Root Directory

Python defaults select `~/.investorch`; tests and embedded callers may inject an explicit root through `load_config(root=...)`.
`paths.root` is bootstrap configuration.
The local `<root>/investorch.toml` must not redefine it.
The root contains local InvestOrch Agent state including:

- `investorch.toml`
- `mcp.toml`
- the configured workspace directory
- the configured state directory

These files are outside the Git repository.

## Root-relative Paths

`paths.workspace`, `paths.state`, `cnequity.config_path`, and `backtest.rqalpha_bundle_dir` are relative to `paths.root`.
They must not be absolute and must not escape the root.
The workspace and state paths must be strict subdirectories of the root and must not equal or contain one another after path resolution.
`backtest.artifact_dir` and `execution.background_job_dir` are relative to the configured workspace and must remain inside it. Path checks resolve symlinks before checking containment.
These paths may be absent when configuration is loaded; the operation that uses one validates its availability or creates the artifact directory.
The SQLite session database is stored under the state directory.
Long-term Agent memory does not use a database.
Long-term memory is maintained as Markdown in the workspace, with `MEMORY.md` as the entry point.

## Configuration Schema

`AppConfig.describe()` returns JSON Schema, including field lifecycle and sensitivity. Settings obtains its field types, enums, defaults and constraints from this description. Schema contains configuration semantics; the frontend owns page layout.

The effective configuration includes the following policy sections; Local TOML only needs to contain explicit overrides:

- `[runtime].default_timezone`
- `[models.<id>]` reusable connection settings
- `[agents.<role>]` connection ID, actual model, effort and window for each built-in Agent
- `[auth]` operator-owned OAuth credential records
- `[activity]` prompt input and label length limits
- `[compaction]` automatic context-compaction policy and summary output budget
- `[permission]` tool approval mode and complete-input limits
- `[tui]` sidebar, composer, Activity, approval, Todo, and Queue dimensions
- `[web]` server, transport, pagination, reconnect, and WebUI presentation settings
- `[backtest]` data paths, artifact path, execution policy, default cash, and inspection limit
- `[cnequity].config_path` and `mcp_cache_tools_list`
- `[execution].default_timeout_seconds`, `max_timeout_seconds`, and `background_job_dir`
- `[explore].max_full_read_bytes`, `max_read_chars`, `max_search_results`, and `max_search_snippet_chars`
- `[calculate].max_expression_chars`, `max_nodes`, `max_integer_bits`, and `max_abs_exponent`
- `[observability].sdk_tracing_enabled`
- `[logging].level`, `max_bytes`, and `backup_count`
- `[mcp].default_timeout_seconds`, `drop_failed_servers`, and `include_server_in_tool_names`

They are validated at startup and again before an update is persisted. `background_job_dir` and `backtest.artifact_dir` must remain relative to the configured workspace.

`models.<id>` registers an arbitrary named connection. `openai_compatible` connections define `base_url` and `api_key_secret`, naming a key in `[secrets]`; `chatgpt` connections define a credential reference and use the official endpoint. `agents.<role>` selects a connection through `model`, an actual model ID through `name`, `reasoning_effort`, and `context_window_tokens`. Exactly five roles exist: `main`, `title`, `activity`, `permission`, and `compact`. Skill Review reuses `agents.permission`; Spawn inherits the parent Active Run model.

Reasoning effort accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`; provider/model support must be checked. Every effective model has a positive context window. Main requires an explicit window; an auxiliary role may inherit it only when both connection and actual model match Main. Changing a Session/Spawn model name requires an explicit window; changing connection also requires explicit name and effort. Do not infer a window from the model name.

Connections and Agent defaults require restart. The Host owns connection clients, while each Run owns a frozen model selection. API keys and OAuth credentials are resolved separately and never persisted in Session model metadata. There is no `providers` layer or `agents.spawned` role.

## Model readiness and Setup

A fresh configuration is structurally valid before all role selections are supplied. `AppConfig.readiness()` identifies missing settings and credential presence before Agent construction. `investorch web` serves Setup without starting the Agent Host. Operators add API-key or ChatGPT connections, authorize ChatGPT plan usage when applicable, then choose each role's model, effort and window. Saving alone does not prove connectivity; the optional test makes a small real request. TUI/plain can use `/model login [connection]` and `/model logout [connection]` for ChatGPT authorization after configuration.

ChatGPT model choices come from the account catalog. A catalog failure is an error to retry, not an empty catalog. OAuth refresh is on demand; temporary failures retain recoverable credentials, and revoked authorization requires login. Usage or access errors never silently switch accounts or fall back to a paid API connection. Identity-only login is insufficient for inference. Credentials remain redacted; do not copy tokens from another application's files.

Every Local TOML is validated against the current Schema. Old `models.main`-style role configuration requires manual offline conversion after a complete backup; there is no automatic TOML migration or `config_version`. Bad TOML, unknown fields, escaping paths or incompatible state require repair and are not treated as a new installation. Session SQLite model metadata upgrades through the normal storage path.

## Tool Permission Review

`permission.mode` selects `manual` or `review` as the startup default. Manual mode sends every SDK tool interruption to the user. Review mode first asks the independent Permission Agent for `APPROVE`, `ASK`, or `REJECT`; `ASK` uses the same human approval interface as manual mode. `/permission [manual|review]` overrides the mode for the current process only and never writes configuration.

The Permission Agent receives the complete active user-authored instruction history and complete tool arguments. Raw instruction history up to `permission.max_user_instruction_chars` is reviewed directly; larger history is sent to a specialized instruction compactor and its output must fit `permission.max_compacted_instruction_chars`, which cannot exceed the raw limit. History is never silently truncated. Missing or corrupt history, compaction failure, invalid or oversized compacted output, excessive tool arguments, and invalid review output all fall back to `ASK`. Review reasons are limited by `permission.max_reason_chars`.

Changing `agents.permission` or its connection to another provider sends both review and instruction-compaction materials to that provider. The Agent-facing configuration tool may read Permission settings but cannot modify `models.*`, `agents.*`, `auth.*`, `secrets.*` or `permission.*`; only the operator can change the subsystem configuration.

## Context Compaction

`/compact` replaces the current OpenAI Agents SDK session history with one high-fidelity assistant continuation summary. It preserves the session ID and title. The append-only JSONL journal is never compacted, so TUI history replay and resumed visual history retain the complete conversation.

The Compact Agent is independent of the Main Agent and has no tools or application context. It receives the complete SDK session history as untrusted data and neither continues the user's task nor writes memory or configuration. The summary is stored as a marked assistant message, not a system, developer, or user instruction. Changing `agents.compact` or its connection to another provider sends the complete SDK session history to that provider.

The hot `[compaction]` section defines:

- `auto_enabled`: whether a completed Main turn may trigger automatic compaction; it does not disable preflight protection or explicit `/compact`.
- `trigger_ratio`: a finite number strictly between zero and one, applied to the active Run context window.
- `max_output_tokens`: a positive integer used as the Compact Agent summary output budget when supported by its provider adapter.

Preflight compaction checks continuation, instructions, tool schemas and incoming input against the selected Run window before Main inference. It uses the existing Run reservation; failure, empty output or a still-oversized request blocks Main. It preserves the complete Journal.

Post-answer automatic compaction runs synchronously after the Main answer and initial title generation, before the next user input is accepted. Its only occupancy signal is the Main Agent's last physical request total; cumulative session usage and Title, Activity, Permission, or Compact Agent usage do not participate. A compaction failure does not discard a completed Main answer. Session replacement retains an in-memory snapshot and attempts to restore it after any replacement exception or cancellation.

Compact Agent usage contributes to per-session auxiliary token totals. After successful manual or automatic compaction, `Main context` is unknown until the next physical Main request supplies real usage; Compact Agent usage and summary character length are not used as estimates.

`runtime.max_turns` is passed explicitly to normal Agent runs, including approval resumes. No Agent run relies on the Agents SDK default turn limit.


## Backtest Configuration

The `[backtest]` section owns application-level RQAlpha data selection, artifact placement, defaults, inspection limits, and execution policy.

Data and artifact keys:

- `use_cnequity` selects the CNEquity data-source overlay, controls whether `inspect_rqalpha_data` is exposed to the Agent, and enables or disables the built-in CNEquity MCP server.
- `rqalpha_bundle_dir` locates the native RQAlpha bundle under `paths.root`.
- `artifact_dir` locates generated backtest artifacts under the configured workspace.
- `default_initial_cash` is used only when a `run_backtest` call omits `initial_cash`; an explicit tool argument wins for that run.
- `inspect_max_symbols` limits one native bundle inspection request.

Execution-policy keys:

- account behavior: `stock_t1`, `dividend_reinvestment`, `dividend_tax_enabled`, `capital_gain_tax_rate`;
- risk validation: `validate_price`;
- matching and limits: `matching_type`, `price_limit`, `volume_limit`, `inactive_limit`;
- slippage: `slippage_model`, `slippage`;
- transaction costs: `stock_min_commission`, `stock_commission_multiplier`, `tax_multiplier`, `pit_tax`.

Initial cash must be finite and greater than zero. Inspection limits must be positive integers. Fee, tax, multiplier, and slippage values must be finite and non-negative. Matching type and slippage model accept only explicitly validated capabilities; arbitrary upstream strings are rejected.

`use_cnequity` and `rqalpha_bundle_dir` are restart-required because they affect process composition and the Agent Tool surface. Artifact placement, default cash, inspection limits, and execution-policy keys are hot: the next backtest or inspection reads their current values.

At the start of each approved backtest, InvestOrch Agent freezes one validated `AppConfig` snapshot. Initial cash, data-source selection, bundle and artifact paths, RQAlpha execution policy, and `request.json` metadata all come from that same frozen snapshot. A concurrent hot update affects a later backtest, never an in-flight run.

Each `request.json` records a focused execution snapshot rather than the entire application config. It includes the effective initial cash, benchmark, data source, strategy hash, engine version, and runtime policy needed to reproduce the run. It does not include secrets or unrelated settings. Returned artifact paths remain workspace-relative.

RQAlpha remains daily-only with a stock account. Run type, frequency, account type, analyser output, progress behavior, persistence, and automatic bundle updates are capability or ownership invariants, not configurable settings.

## CNEquity Configuration

The `[cnequity]` section contains application-owned CNEquity integration policy:

- `config_path` is root-relative and identifies the config used by both the CNEquity MCP process and the RQAlpha overlay.
- `mcp_cache_tools_list` controls tools-list caching for the built-in CNEquity MCP connection.

Both keys are restart-required. The config path may be absent during generic configuration loading; an operation that requires CNEquity validates the file and data coverage. CNEquity dataset names, exchange mapping, and adjustment-factor semantics are adapter contracts and remain in code.

The built-in CNEquity MCP server is composed only when `backtest.use_cnequity=true`. With `false`, InvestOrch Agent neither constructs nor starts that server; independently configured MCP registry entries are unaffected.

## Observability Configuration

`observability.sdk_tracing_enabled` uses positive `enabled` semantics and is restart-required because SDK tracing is initialized globally at process startup. Agent runs use the same process AppConfig policy.

## User Interface

`investorch` starts the Textual chat workspace by default. The session list occupies the full workspace height below the header. The conversation pane to its right contains the timeline and composer, so the input region never extends underneath the session list. User and assistant messages remain the primary timeline, with raw reasoning, tool arguments, approval state, and tool output inside collapsible activity steps. Approval requests replace the normal composer controls in the current input region; they do not open a modal screen.

The Session Active/Effective model supplies the Main Agent capacity shown by the interface. The `[activity]` section owns Activity Agent prompt truncation and label length limits. The `[tui]` section owns user-adjustable sidebar and composer dimensions, message-author spacing, run-timer refresh frequency, interaction-panel height, Queue height and preview count, Todo heights, and maximum heights for the Activity title list, Activity detail, and inline approval arguments. `message_author_margin_top` must be greater than `message_author_margin_bottom`, so each `You` or `InvestOrch Agent` heading has stronger separation from the preceding timeline content than from its own message body. While an Agent run is active, the header shows elapsed wall-clock time; approval waiting remains part of the same run and does not reset the timer. The timer disappears when the run returns to Ready. TUI settings are restart-required because the widget tree and timers apply them when the application mounts; Activity limits are hot and apply to the next generated label.

The `[web]` section owns the default loopback port, per-connection event queue capacity, history page size, WebSocket reconnect delays, retained notice count, composer height, unused-session discard delay, and active-run timer interval. The Web bootstrap response exposes only the validated WebUI subset; the frontend has no independent fallback defaults. `investorch web --port` overrides `web.default_port` for one process without changing AppConfig. The server remains loopback-only as a security invariant. Most Web settings require restart; `web.theme_mode` applies live. Use Schema lifecycle metadata for the field being changed.

`investorch --plain` starts the verbose Console fallback. Plain mode renders raw reasoning and tool output directly; it does not create the Activity Agent, generate activity labels, or backfill labels for historical sessions.

Activity labels are presentation-only descriptions of what the Main Agent is doing. They do not enter the Main Agent context and do not replace raw execution content.

The TUI shows per-session token usage observed during the current application process: requests, input, cached input, cache-write input, output, reasoning output, and total tokens. These totals include Main Agent runs plus Title, Activity, Permission, and Compact Agent calls made for that session. Cached and cache-write counts are input-token breakdowns, and reasoning is an output-token breakdown; they are not added to total a second time. `Main context` compares the Main Agent's last physical request total with the active Run model window; cumulative multi-request totals are deliberately not used as context occupancy. Usage display state is not persisted across application restarts.

## Log and Session State Ownership

The `[logging]` section controls the application diagnostic log level, rotation size, and retained backup count. These settings are restart-required. The active UTF-8 log is stored at `<state>/logs/investorch.log`; it contains application lifecycle, compaction metadata, and failure diagnostics, not full user messages, reasoning, assistant answers, compact summaries, tool arguments, tool output, or secrets.

Each chat session has an authoritative append-only event journal at `<state>/sessions/<full-session-id>.jsonl`. Journal records preserve raw user messages, semantic Agent output, and approval decisions with per-session sequence numbers. Slash commands are not chat events.

An `activity_label` record is a derived display annotation. Its `target_seq` refers to the `seq` of the corresponding `tool_called` record. Labels may arrive later than their target and may be deleted without changing execution truth or model continuation. The Textual history view reads the complete JSONL file directly, uses the last valid label for a target, and falls back to the raw tool name when no label exists.

`<state>/sessions.db` remains owned by the OpenAI Agents SDK for model conversation continuation. Context compaction modifies only this model-continuation history through the SDK's public Session API. Existing application session metadata remains there for now, but the database is not the journal. There is no UI projection, search index, or activity cache database.

## Local Configuration

Machine- and user-specific overrides live in:
`<root>/investorch.toml`
Secrets live in its `[secrets]` table.
Secret values must never be copied into workspace memory.

## Reading Configuration

Use `get_config` to inspect effective application configuration.
Secret values returned to the Agent are redacted.
Prefer `get_config` over memory whenever the current effective value matters.

## Modifying Configuration

Use `update_config` for Agent-managed changes to normal application configuration.
Configuration modification requires user approval.

Rules:

- `paths.root` cannot be changed at runtime.
- Restart-required keys must use `persist=true`; the returned result reports `requires_restart=true` and `applied=false`.
- Hot keys report `applied=true` when the current runtime is updated.
- Unknown normal configuration keys cannot be created.
- Values must retain their expected type. Strings, booleans, integers, and floating-point values are supported.
- Root-relative paths are validated before being applied.
- Runtime policy values must satisfy their type, range, and cross-field constraints before being applied.
- `persist=true` validates the whole candidate before atomically replacing `<root>/investorch.toml`; failed validation or writes preserve prior state. Changes made outside the process require reload/restart, and conflicting writes are rejected.
- `persist=false` only changes the current `AppConfig`.

The Agent-facing config tool cannot modify `models.*`, `agents.*`, `secrets.*`, `auth.*`, `permission.*`, or existing Session model selections. These belong to the operator.
Operators replace secrets through Setup or Settings, or edit Local TOML while the application is stopped. Reads expose only masks or configured status. AppConfig itself permits valid secret and Permission updates; the Agent Tool enforces the caller-specific prohibition.

`update_many` validates a batch as one candidate. Settings reset removes Local Overrides so the current Schema default applies. Restart-required changes remain saved next-start values; `requires_restart` describes the current operation, not a global restart tracker.

`AppState` holds temporary reasoning-effort, Permission and follow-up overrides. Without an override it reads the effective configuration; `/defaults`, `/effort` and `/permission` affect future Runs without changing Local TOML or AppConfig. Each Run captures its own immutable options.

## Application MCP Policy

The `[mcp]` section in application configuration owns process-level MCP policy:

- `default_timeout_seconds` supplies the timeout only when a registry entry does not define one;
- `drop_failed_servers` controls whether application startup continues without failed MCP servers;
- `include_server_in_tool_names` controls the main Agent's MCP tool namespace.

These keys are restart-required because connections and the Agent Tool surface are composed at process startup.

## MCP Server Registry

MCP server configuration is local instance state and is stored separately at:
`<root>/mcp.toml`
It is not tracked by Git.
The currently supported transport is:
`streamable_http`
Use:

- `list_mcp_servers` to inspect configured servers
- `configure_mcp_server` to add or update a server
- `remove_mcp_server` to remove a server

Agent MCP configuration changes require user approval. Operators may manage the same registry in Settings → Integrations. Agent and Web use shared domain validation and serialization. Edits persist for the next application restart; the page reports runtime status as unknown when no connection has been observed.

## MCP Secrets

Real secret values should not be written directly into `mcp.toml`.
Reference configured secrets with placeholders such as:
`${TAVILY_API_KEY}`
The real value belongs in the `[secrets]` table of `<root>/investorch.toml`.
MCP loading expands these placeholders from AppConfig secrets.
Per-server URLs, headers, enabled state, timeout, and tools-list caching remain registry concerns in `mcp.toml`; they are not duplicated in AppConfig. Public reads mask literal header values and URL userinfo/query values. Omit unchanged URL/header fields when editing; replace them with complete new values to change them. Display masks are rejected as replacements.

## Initialization

Persistent local runtime state is initialized separately from configuration loading.
Fresh initialization creates MEMORY.md only when absent and installs the six bundled Skills into workspace/skills with state/skills.json registration. Normal startup preserves workspace content. `investorch --update` replaces only managed built-ins from the package, preserving enabled state; restart to use the new catalog.

## Management and Recovery

- Settings owns persistent configuration and MCP entries; lifecycle metadata explains when changes apply.
- Skills supports read-only inspection and explicit enable/disable/custom removal. Create, Install, Edit and Fork open normal Agent Sessions with the existing independent Skill Review and approval chain.
- System Status and offline `investorch doctor` share read-only checks. `investorch upgrade check` adds upgrade guidance, including managed built-in differences and Portfolio migration requirements. They do not initialize or repair state.
- `investorch --update` updates only package-managed built-ins, retaining their enabled state and leaving custom Skills and user Memory intact. It does not initialize the Portfolio database.
- Backup creation and verification are offline CLI operations. Default archives exclude raw credential configuration; restore follows the operator recovery guide in a separate root with the application stopped. Portfolio schema migration remains owned by the existing Portfolio migration commands.

## Images

Use `get_config` for effective `images.max_images_per_input`, `images.max_image_bytes`, `images.max_total_image_bytes` and `images.default_detail`. Byte limits count decoded image bytes. Detail accepts `auto`, `low`, `high`, `original`. All `images.*` settings require persistence and restart; Web bootstrap and backend validation share the running snapshot. Workspace `explore` image reads use the same per-image limit and detail, while text reads retain `explore.*` limits.


## Session model selection

The Host shares one `SessionModelStore` across interfaces. Its views distinguish Persisted, Draft, Effective and Active. Resolve Draft first, then persisted Session values, then `agents.main` for new or legacy unbound Sessions. `/model` and `/effort` edit only the selected Session Draft. Drafts are process-local until a new Run journals input and persists the frozen model before inference. A metadata failure can leave already-recorded input; it does not allow inference. An active Run remains unchanged, and only the admitted Draft revision is cleared.

Queue resolves the latest Session model on promotion; Steer keeps the current Run model. Permission/follow-up defaults retain their submission-time behavior. Fork copies persisted model state and preserves an uncommitted source selection as a target Draft. Spawn inherits its parent's Active model, with overrides affecting only the child. Archived Sessions permit Draft edits but cannot execute before restoration. Deletion cleans shared Draft state and rejects unsafe referenced-connection deletion.

Each new Run strips opaque reasoning and provider response-item IDs from the request copy of prior continuation, preserving messages and function `call_id` links. Stored continuation and Journal originals remain intact. This also applies to unchanged connections, so prior reasoning reuse and caching may differ across Runs.
