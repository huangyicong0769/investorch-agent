# Runtime / Session Execution Model

[简体中文](Runtime_Session_Execution_Model.zh-CN.md)

## Scope

This document defines execution, lifecycle, Session access, and Agent message submission contracts. The higher-level component map is in [Architecture](InvestOrch_Agent_Architecture.md).

## Identities and ownership

InvestOrch separates:

- **Session**: persistent conversation identity.
- **Run**: one transient top-level Agent turn.
- **Selection**: the Session displayed by a client.

Changing Selection never switches, cancels, or mutates an active Run.

```text
Application Host
├── AppState
│   ├── selected_session_id       # TUI/plain selection
│   └── future-run defaults
├── AgentRuntime
│   ├── Session A -> ActiveRun A1
│   ├── Session B -> idle
│   └── Session C -> ActiveRun C1
└── ExecutionState
    ├── shared workspace sandbox
    └── global managed jobs
```

The Web client sends explicit Session IDs and does not depend on the host's selected Session. `AppState` stores IDs and defaults, not an open SDK Session handle. Every Run opens and closes its own temporary `SQLiteSession`.

## Application boundary

Normal input enters `application.interaction.submit_user_input()` with an explicit Session ID. It decides whether to start a Run or submit a Steer/Queue follow-up and returns an acknowledgement with stable ownership IDs.

Session mutations enter `application.sessions.SessionOperations`. It coordinates Runtime maintenance reservations, SDK continuation state, metadata, and the journal for create, unused cleanup, title, archive, unarchive, fork, clear, and compact. Presentation clients own command parsing, selection, and user-facing notices.

Agent `list_sessions` and `read_session` delegate to stateless functions in `application.session_queries`. The Host binds both queries to its live AppConfig, listing to its database and current Runtime, and history to its database and existing SessionJournal. Each call snapshots the hot session_tools defaults and budgets; omitted or null limits inherit the current configured default. It registers the bound Tools once after Runtime construction and before yielding; AgentContext and AgentLoop carry no Session-read capability or provider. Listing calls the public Runtime snapshot for each target Session; history needs no Runtime. Tools only forward public arguments. List status summarizes the current process: an active Run's phase, `pending` for accepted Queue/Steer work without an active Run, or `idle`. It omits queue internals and historical Run outcomes. Exact-ID reads provide history only, including self, active and archived Sessions, without selection changes, Run creation or maintenance reservations. See [Session access](InvestOrch_Agent_Architecture.md#session-access-030-stage-a) for Tool arguments, bounded results and paging examples.

`presentation.py` converts application values into explicit JSON-safe projection dictionaries.

## Run lifecycle

`AgentRuntime.start_run()` synchronously:

1. rejects a second top-level Run for the same Session;
2. creates a unique Run ID;
3. resolves the latest Session model Draft and captures immutable `RunOptions`;
4. registers and starts an `asyncio.Task`.

The task opens the SDK Session, journals accepted input when needed, then persists the frozen model before calling `AgentLoop`. Journal and SQLite model metadata are separate commit boundaries: a metadata write failure blocks inference but may leave input already recorded. Draft cleanup removes only the admitted revision. Preflight compaction runs before Main inference; SDK Session and Runtime registry entries close in `finally`.

Different Sessions may run concurrently. Manual compaction, clear, fork, archive, and a top-level Run are mutually exclusive only for the same Session through a maintenance reservation.

Failed Runs carry an optional public `error` in their runtime event and durable `run_ended` record. Provider validation messages and known connection failures are bounded and stripped of credentials before either is emitted; arbitrary exception bodies stay in local logs. Compaction failures identify their stage. Web shows the same cause live and after reloading history. Older outcomes may receive an append-only `run_error` diagnostic keyed by Run ID after their cause has been verified from logs; the timeline merges the latest diagnostic into the original outcome without moving or duplicating it.

## Future defaults and Run snapshots

The Host-owned `SessionModelStore` exposes Persisted, Draft, Effective and Active model views. Resolution is Draft → last persisted model → `agents.main` for a new or legacy unbound Session. All effective models have a positive window. Session edits are process-local until a new Run commits them; exit may discard unused Drafts. An active Run keeps its frozen connection, actual model, effort and window.

Permission mode and follow-up behavior retain submission-time defaults. Queue does **not** freeze the model when submitted: promotion resolves the Session’s latest Draft. Steer continues the active model; terminal Steer fallback is a new Run and resolves the current Session model then. Credentials are fetched per request and never stored in RunOptions.

## Follow-ups

### Steer

Steer remains part of the same top-level Run:

1. reserve the input in FIFO order;
2. append `user_steer` to the Session journal;
3. ask the current streamed turn to stop at a safe boundary;
4. after any approval is resolved, continue from SDK state with the same Run ID.

If the SDK result is already terminal, the Steer is promoted to a subsequent Run without duplicating its journal record.

### Queue

Queue is an application-level per-Session FIFO outside SDK continuation state. Submission stores intent but does not yet create a conversation event. After a successful Run, the oldest item is journaled once as `user_message` and promoted to a new Run.

A stopped or failed Run pauses the remaining queue. Resume promotes one head item; Clear discards unpromoted intent without changing conversation history.

## Agent message submission

After sender approval, `send_message` uses the same `submit_user_input` routing. An idle target starts a Run; an active target's frozen `RunOptions` select Steer or Queue. Future defaults apply to promoted Runs. The sender receives `UserInputSubmission` after acceptance, without awaiting the target's result:

| Disposition | Acceptance boundary | Persistence |
| --- | --- | --- |
| `run_started` | Runtime schedules a new top-level Run | Initial `user_message` is written asynchronously; the receipt is not a Journal commit |
| `steer_submitted` | Existing journal-first Steer submission completes | Steer is recorded, but consumption still awaits a safe boundary |
| `queue_submitted` | Runtime accepts an in-process FIFO item | No message history until promotion; no crash/restart recovery |

Source Session/Run IDs travel inside immutable `UserInput.origin`, separately from the target Run ID. Initial input, Steer continuation, Queue promotion, and terminal Steer fallback use the same SDK conversion and Journal event types. Effective Agent instructions participate in the same frozen Review history. A generic `user_input_committed` view notification is emitted after `user_message` is readable; history/live reconciliation uses Journal sequence numbers.

Self, missing, archived, stopping, and reserved destinations or unresolved pending work fail through existing lifecycle rules. There is no retry queue, automatic reply, completion callback, or persistent mailbox. Cancelling a sender during pending approval leaves the target untouched; after submission, the target owns its accepted input and Run. Fork copies optional origin with history; Compact continues to leave Journal unchanged.

## Stop and cancellation

Stopping a Run changes its phase to `stopping`, closes follow-up submission, pauses retained Queue intent, and cancels the Run task. Cleanup follows the normal `finally` path. Pending manual approval is cancelled without fabricating an approve/reject decision.

Managed background jobs retain application ownership after their originating Run ends or is cancelled.

## Todo state

Todo is transient Run state projected to clients. It is not stored in SDK continuation, the JSONL journal, Session metadata, or a fork. A new Run begins with an empty Todo snapshot.

## Session lifecycle

- Create makes a new persistent identity.
- Rename updates Session metadata.
- Archive preserves continuation, journal, title, lineage and model selection. Model Draft edits remain allowed, but execution waits for restoration.
- Unarchive restores normal operations.
- Delete removes the explicit Session and all recursive Spawn descendants. Clear uses that same deletion contract, then creates a fresh ordinary replacement without inherited lineage.
- Compact replaces SDK continuation with a marked assistant summary; the journal remains complete.
- Fork clones the source's last stable committed head into a new independent Session.

Fork copies SDK continuation, the journal when present, title with a suffix, direct parent lineage and persisted model metadata. A source Draft becomes a target Draft, retaining its process-local semantics. It does not copy active Runs, permission/follow-up overrides, Steer/Queue state, Todos, approvals, archive state, jobs, usage, caches, or UI state. It is stable-head cloning, not historical-turn branching.

Empty Sessions that never acquire SDK messages, journal records, metadata, queued intent, or lineage are discarded. Web starts without creating a Session until the user does so; TUI/plain create an initial Session.

## Output and replay

Every semantic `OutputEvent` is wrapped with Run and Session ownership. The application appends it to the originating Session's JSONL journal before projecting it live.

The SDK SQLite Session is model-continuation state and may be compacted. The JSONL journal is append-only user-visible replay state. Inactive Session output is still journaled. Clients reconcile paged history and live events by original journal sequence.

Activity labels are asynchronous derived annotations targeting a Tool-call sequence. Missing labels fall back to the real Tool name and do not change execution truth.

Agent Conversation history is a separate flat projection: accepted user messages, proven activated Steers as ordinary user records, assistant messages and tool calls/results. Reasoning, approvals and pending/discarded Steers remain outside this view; optional raw Events retain their original fields within the response budget. Images in Conversation are metadata only. A page freezes a committed `snapshot_seq` and continues with exclusive `next_before_seq`; disposition evidence is interpreted through that entire snapshot, including proof later than the page cursor. Journal snapshot capture holds a short lock, and subsequent scans exclude later appends. Empty history has `snapshot_seq=0`. Web/TUI replay and Review's strict authorization policy retain their own existing contracts.

## Approval

Every `ApprovalRequest` carries an immutable approval ID, Run ID, Session ID, and captured permission mode. Resolution targets that exact request.

- `manual`: always ask the user.
- `review`: the Permission Agent may approve, reject, or ask; failure and invalid output fall back to asking.

The approval decision is journaled for the originating Session. Multiple Sessions may wait for approval concurrently.

## Client projections

Web and TUI are first-class projections of the same application state.

- The TUI can switch Sessions while Runs continue elsewhere and combines full journal replay with live state.
- Web uses REST for snapshots and mutations, paged history for replay, and WebSocket events for live changes.
- Both expose Session state, Run status, Steer/Queue behavior, approval, archive/fork/compact operations, managed jobs, and usage according to their UI design.
- Plain console awaits one Run before reading the next input, so it cannot provide live cross-Session interaction.

No client owns a second copy of execution truth.

## Usage and compaction

Main and auxiliary model usage is accumulated separately per Session for the current process. Each completed model request updates the shared presentation snapshot immediately; SDK cumulative usage is reconciled at exit so later failure or cancellation does not discard earlier requests. Auxiliary usage includes Title, Activity, Permission, Skill Review and Compact Agents, including failed output validation. A request that never reports usage cannot supply a known token count. HTTP, WebSocket and TUI consume the same process epoch and monotonic snapshot revision.

Main-context occupancy is labeled measured after a physical Main response (input plus output usage) and estimated before a request or after compaction. Estimates include the portable request history, instructions, tool schemas and live Todo; auxiliary tokens never contribute. The model name and context-window denominator travel with the snapshot.

Preflight checks continuation and incoming input against the selected Run window with conservative overhead/output allowance. When needed, it compacts inside the existing Run reservation. Failure, empty summary or a still-oversized budget blocks Main; replacement failures attempt restoration. This protection also applies when `compaction.auto_enabled` is off.

Every model request is checked again after tool results and activated Steers arrive. With automatic compaction enabled, crossing the soft threshold summarizes a closed history prefix while preserving current Run user/developer instructions and, when it fits, the latest complete tool exchange. A Run-local input projection applies the summary on all subsequent requests and approval resumes, without changing SDK execution state or replaying tools. After the SDK has stopped writing, the matching prefix is replaced in SQLite while preserving the uncompressed suffix. A mismatched prefix is retained and logged. If soft compaction fails and the original request still fits the input budget, execution continues; an unsafe request is not sent. Estimates and output allowance are conservative guards, not provider tokenization guarantees.

Post-answer automatic compaction uses the active Run window and last Main request usage; failure does not discard an already completed answer. Manual and automatic compaction change SDK continuation only, preserve the complete Journal and charge Compact requests to auxiliary totals as they complete. Successful compaction immediately publishes a new context estimate. The compact model's own input budget is checked and oversized text is summarized in bounded batches.

Each new Run sends a copy of pre-existing history with opaque reasoning and provider response-item IDs removed, preserving messages and function `call_id` links. SQLite and Journal originals remain unchanged, and reasoning generated within the Run remains available to continuation. This applies to same-connection Runs too: opaque reasoning is not reused across Runs, with possible continuity/cache costs.

## Background jobs

`ExecutionState` owns managed jobs globally. Each job records owner Session and Run IDs for attribution. Job listing is application-global, and jobs may outlive their creator Run.

## Shutdown

Normal client exit is blocked while active Runs or queued follow-ups remain. Defensive shutdown cancels remaining Run tasks, waits for normal cleanup, closes the shared execution environment and MCP manager, and discards unpersisted transient queues.

## Current limits

The application does not persist active Runs, Session model Drafts, pending Steer, Queue state, Todo, pending approvals, usage display state, or UI state across process restarts. All Sessions share one workspace and managed-job registry. There is no historical-turn branching, per-Session workspace, distributed scheduler, or cross-process Runtime recovery.

## Image input and replay

Run, Steer and Queue carry the same `UserInput`; Portfolio Ask keeps developer context separate. Queue promotion journals the complete input. Steer disposition records reference the original sequence without duplicating images. Public legacy text calls normalize once; all user model input uses structured content. Developer-only application runs have no user payload.

Journal `images` fields are additive on user messages, steers, assistant messages and tool outputs. Missing fields in older history mean no images; no migration is required. Fork copies inline SDK/Journal content and archive preserves it. Delete/Clear remove the old scoped histories; Clear then creates a fresh replacement. Web drafts remain Session-scoped and optimistic messages retain images until durable events arrive.

## Session spawning and global capacity

Spawn inherits the parent Active Run model, never its newer Session Draft. Same-connection name changes require an explicit window; changing the connection also requires name, effort and window. Only the new child receives overrides. Ordinary and spawned Sessions share model persistence, Draft editing and deletion cleanup.

`spawn_agent(task, title?)` uses existing Permission/HITL approval and ordinary `submit_user_input`. Children inherit the parent Run model by default, with the same Tools and Skills and independent approval rules, with separate continuation and Journal. Initial input remains `user` role with trusted SDK Session/Run/call provenance. The creation transaction records independent Spawn lineage and a one-time copy of related Portfolio IDs; it copies neither parent history nor Portfolio/Ledger facts or authorization. Forks do not inherit Spawn lineage, and parent completion, stop or archive never cascades to children.

The tool-call identity yields a stable child ID. Existing workflow acceptance, Runtime and Journal facts prevent duplicate initial submission. Proven capacity/shutdown refusal rolls back an unstarted child; uncertain acceptance retains its identity for diagnosis. Receipts confirm submission, not completion or restart recovery. Recursive children use the same tools; no stop/wait/status Tool, completion registry or automatic reply is added. Observe state through `list_sessions` and completion facts through `read_session(view="events")`.

All new top-level Runs share the synchronous admission gate in `AgentRuntime._start_run()`. `runtime.max_concurrent_runs` defaults to 3 and accepts strict positive integers. Settings or local TOML may hot-apply it; reducing the limit leaves existing Runs untouched and rejects new admission with `capacity_exceeded` (Web HTTP 429). Parents, ordinary input, messaging, Spawn and follow-up promotion use the same slots. Waiting Approval and Stopping continue to occupy slots until removal. Auxiliary Title/Permission/Activity/Compact calls consume no additional slots. The policy is separate from frozen `RunOptions`.

Capacity refusal restores and pauses the original Queue head with its captured options before journaling. Explicit Resume reports the capacity error and retains the paused queue. Durable terminal Steer fallback keeps its original seq and retries through existing promotion when another Run or reservation ends. Synchronous registration before promotion awaits prevents duplicate consumption. Neither refusal changes an already completed Run into failure; no global queue or timer is added.

Explicit Delete/Clear previews all affected titles and blockers. Multi-Session Clear requires the complete scope and an irreversible-deletion warning: Web/TUI use a confirmation dialog, and Plain accepts only `DELETE` (other input or EOF cancels). Cancel/Escape preserves identities, histories and selection. Confirmation waits hold no lifecycle lock. Submission locks application lifecycle mutation and reserves the complete Spawn scope in stable order. Active, waiting-approval, stopping, maintenance or pending-input nodes block the entire operation without automatic Stop. Independent Fork references outside the scope also block it. Every client submits the displayed Session IDs; changed descendants require renewed confirmation. A Clear call without an explicit scope approves only its target, preventing unexpected cascade. Generic unused cleanup cannot remove Spawn nodes. Archive/unarchive remain independent.

One guarded SQLite transaction removes all scoped continuation, association and lineage rows, with per-Session Journal fences cancelled on precommit failure. Precommit failure preserves all identities and histories. Postcommit JSONL/presentation cleanup reports actual deleted IDs. A cleanup failure returns `session_cleanup_failed` with `consistency_uncertain=true`; explicit cleanup retry accepts only absent identities. If cleanup succeeds but Clear cannot create its ordinary replacement, `session_clear_replacement_failed` reports the committed `deleted_session_ids`, `replacement_session_id:null` and `stage:replacement_creation`. Neither failure rolls back the database. Clients remove deleted caches and selection, disable the deleted conversation, and offer deliberate recovery (TUI `Ctrl+N`, Plain `/new`, Web New Session or Retry cleanup). Cancellation waits for replacement creation to settle, and committed deletion/replacement logs support resynchronization after a disconnected response. Portfolio, Workspace, Skill and external artifacts remain outside the deletion scope.

Web replaces the full-width Portfolio banner with a compact right-hand context card for related Portfolio references, direct children and independent parent/Fork navigation. Narrow screens use an accessible Context dialog. Current Runtime phases take precedence; Completed/Failed/Cancelled require a trusted latest `run_ended` with no newer turn activity. Idle alone means Idle. The sidebar stays flat, and children open complete ordinary conversations. TUI shows the parent's short ID in existing Session metadata.

Ordinary WebSocket Runtime updates patch existing Context query entries without rereading Journal. Empty Runtime snapshots preserve a proved terminal result; new input clears it to Pending until current Runtime/Journal facts arrive. Input commits and Run endings invalidate the affected Session and parent Context, with a discovery refresh when new-child metadata is unavailable; reconnect resynchronizes all Contexts. A slow or first HTTP read merges Runtime snapshots received during that request and rereads Journal if an included Session ended meanwhile, even before its parent metadata is available. Temporary listeners are discarded when the read settles. Historical outcomes come from Journal.
