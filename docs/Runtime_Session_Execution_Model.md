# Runtime / Session Execution Model

## Purpose

InvestOrch Agent separates three identities that were previously represented by one mutable session handle:

- **Session** is the persistent conversation identity.
- **Run** is one transient top-level Agent turn.
- **Selection** is the Session currently displayed by the UI.

Changing the selected Session does not switch, cancel, or otherwise mutate an active Run.

`AgentRuntime` is an application-level task and ownership boundary around the OpenAI Agents SDK. The SDK still owns Agent execution, handoffs, interruption continuation, and conversation history behavior.

## Ownership

```text
Application
├── AppState
│   ├── selected_session_id
│   ├── main_reasoning_effort   # future-run default
│   ├── permission_mode         # future-run default
│   └── follow_up_behavior      # AppConfig-backed future-run default
├── AgentRuntime
│   ├── Session A -> ActiveRun A1
│   ├── Session B -> idle
│   └── Session C -> ActiveRun C1
└── ExecutionState
    ├── shared workspace
    ├── shared sandbox
    └── global background jobs
```

`AppState` does not retain a `SQLiteSession` handle. A Session is stored as an ID; `AgentRuntime` opens one temporary `SQLiteSession` for each Run and closes it in `finally`.

`AppConfig` owns the process default for future follow-ups through
`interaction.follow_up_behavior`. `AppState.follow_up_behavior` is a view over that
configuration value rather than a second source of truth.

## Shared application and presentation boundaries

Normal interactive input enters `application.interaction.submit_user_input()` with
an explicit `session_id`. That use case owns the archive, active-Run, pending-Steer,
and queued-input routing decision and returns a small acknowledgement identifying the
Run and optional follow-up. It snapshots the current future defaults into
`RunOptions`; `AgentRuntime` remains the execution and ownership authority. Selection,
slash parsing, notices, and widget updates remain client concerns. The sequential
plain console intentionally continues to start and await one Run at a time.

Cross-layer Session mutations enter `application.sessions.SessionOperations` with an
explicit Session ID. It coordinates Runtime maintenance reservations, storage,
journal, and SDK Session mutations for create, unused cleanup, archive, unarchive,
fork, title, clear, and compact. It never reads or changes
`AppState.selected_session_id`; command adapters own prefix resolution, selection,
and user-facing text. Storage-only Session queries remain direct read APIs rather than
being wrapped in a repository layer.

`presentation.py` is the transport-neutral projection boundary. It converts Runtime
callbacks, approval lifecycle values, Session records, usage/compaction values, and
journal pages into explicit JSON-safe dictionaries. `OutputEvent` has one public
serializer shared by journal history and live projection. The presentation boundary
does not publish events, manage connections, or introduce a transport framework.

## Run lifecycle

`AgentRuntime.start_run()` synchronously:

1. rejects a second top-level Run for the same Session;
2. generates a unique `run_id`;
3. snapshots reasoning effort, permission mode, and follow-up behavior into `RunOptions`;
4. creates and registers an `asyncio.Task` without awaiting.

The task then:

1. opens the Run-owned `SQLiteSession`;
2. records the user message in the Session journal;
3. invokes the shared, reentrant `AgentLoop` with run-scoped callbacks;
4. closes the Session handle and removes both runtime registry entries in `finally`.

Directly starting a second top-level Run for the same Session is rejected. Queue
behavior is an explicit follow-up route described below, not an implicit side effect
of calling `start_run()` while busy. Different Sessions may run concurrently.

## Interaction defaults and Run snapshots

Reasoning effort, permission mode, and follow-up behavior are future-run defaults.
`/effort`, `/permission`, and `/followup` update those defaults; they do not mutate an
already-active Run. `/followup` updates the current process's AppConfig-backed default
without rewriting the active Run's `RunOptions`.

Each `ActiveRun` owns an immutable `RunOptions` snapshot. Its follow-up behavior
therefore remains `steer` or `queue` for the entire Run even if the user changes the
future default in another command or Session. A queued follow-up separately captures
the then-current future `RunOptions` at submission time so its promoted Run is not
affected by later default changes.

`RuntimeSessionSnapshot` is the presentation boundary for transient interaction
state. It exposes the active Run identity and phase, the active Run's frozen follow-up
behavior, queued and pending-Steer counts, queue pause state, and the active Todo
snapshot. It does not persist that state.

## Follow-up behavior

Normal TUI input sent while the selected Session has an active Run follows that Run's
frozen behavior. There are no separate `/steer <message>` or `/queue <message>`
commands.

### Steer

Steer cooperatively adds input to the same top-level Run:

1. `submit_follow_up()` reserves a FIFO Steer entry in the Run's `RunControl`;
2. the input is appended to the Session journal as `user_steer` and becomes ready;
3. the current streamed Agent turn is asked to stop at its next turn boundary;
4. after pending tool approvals are resolved, `AgentLoop` converts the result to SDK
   state, adds the ready Steer inputs in FIFO order, and continues with the same
   `run_id` and captured Run settings.

A Steer submitted while the Run is waiting for approval remains pending until that
approval is resolved. It does not take ownership of, or answer, the approval.

If the SDK result is already terminal and cannot accept the input, the Steer entry is
promoted to a subsequent Run in the same Session. It is not journaled a second time;
the existing `user_steer` record remains the user-visible source. The options captured
with the Steer submission are used for that fallback Run.

### Queue

Queue is an application-level, per-Session FIFO. It is intentionally outside
`AgentLoop` and the SDK conversation state.

Submitting a Queue follow-up stores a `QueuedInput` containing a stable queue ID, the
text, creation time, and captured future `RunOptions`. Submission alone does not add a
conversation turn or journal record. When the active Run completes successfully, the
oldest item is removed, recorded as a normal `user_message`, and started as a new Run
behind a start gate. This ordering makes the journal commit precede Agent execution
and prevents duplicate replay records.

Stopping or failing a Run pauses any remaining queue before promotion. Paused items
retain their original FIFO order and captured options. The TUI Queue panel can resume
the queue or clear it; resuming promotes exactly one head item and later completions
continue normal FIFO promotion. Clearing removes the pending application-level intent
without changing already-committed conversation history.

## Stop and cancellation

`cancel_run()` is scoped to the selected Session's active Run. It changes the phase to
`stopping`, closes further follow-up submissions, records the pending-Steer count,
pauses a non-empty Queue, and schedules cancellation of the Run task. `stopping` is
not overwritten if an in-flight approval callback unwinds at the same time.

Cancellation uses the normal Run `finally` path: the Run-owned `SQLiteSession` is
closed, registry entries are removed, not-yet-applied Steer entries are discarded,
and a terminal notification is emitted. The runtime first waits for any already
reserved Steer journal write to reach a disposition, so cancellation cannot leave a
half-recorded submission.

If the Run is waiting for manual approval, cancelling the Run cancels that wait and
the TUI removes its pending approval entry. No synthetic approve or reject decision is
created. Managed background jobs are application-owned and are not terminated merely
because their originating Run is stopped.

Queue intent survives Stop but remains paused until an explicit Resume or Clear. This
prevents Stop from appearing to fail by immediately starting the next queued Run.

## Todo state

The Todo tool updates the current Run's `TurnState` and publishes a defensive copy to
the matching `ActiveRun`. Runtime snapshots carry that copy to the TUI Todo panel.
Todo state is transient execution state: it is not written to the SDK Session, the
JSONL journal, Session metadata, or a fork. A new Run starts with an empty Todo
snapshot. The TUI may retain the most recent completed Run's snapshot for presentation
until a later Run replaces it.

## AgentLoop concurrency

`AgentLoop` stores only stable dependencies. Each invocation receives:

- `run_id` and `session_id`;
- the shared `ExecutionState`;
- the captured reasoning effort;
- run-scoped output and approval callbacks.

The Main Agent is cloned with the captured model settings for every Run. The same clone is reused across all interruption continuations in that Run. The shared Main Agent object is never mutated.

SDK handoffs and agents-as-tools remain inside one top-level Run and share its `AgentContext`, including `session_id`, `run_id`, and `ExecutionState`.

## Session commands

- `/new` creates and selects a new Session identity. Existing Runs continue. A Session that is left without SDK messages, journal records, metadata, queued intent, or fork lineage is discarded automatically.
- `/resume` changes only `selected_session_id`.
- `/fork` clones the selected Session's stable head and selects the new Session.
- `/effort`, `/permission`, and `/followup` change defaults for future Runs only.
- `/stop` requests cancellation of the selected Session's active Run.
- `/archive` archives the selected idle Session when it has no queued follow-ups.
- `/unarchive` lists archived Sessions or restores and selects one by ID prefix.
- `/title` edits metadata for the selected Session.
- `/clear` rejects when the selected Session is active, queued, or archived.
- `/compact` rejects when the selected Session is active or archived. An idle
  Session may be compacted while its queue is paused or non-empty because those
  inputs have not entered continuation history.
- `/exit` and Ctrl+Q reject normal exit while any Run or queued follow-up remains.
- `/ps` remains global across Sessions.

Application startup removes legacy unused Session identities. Plain and TUI modes then create an initial Session and remove the selected Session at shutdown if it remained unused. Web mode starts without creating or selecting a Session; `/` remains sessionless until the user explicitly creates or selects one. Sessions with user-visible or continuation state are preserved.

Manual compaction, clear, fork, archive, and a top-level Run are mutually exclusive for
the same Session through one Runtime maintenance reservation. Operations on different
Sessions remain independent. Auto compaction remains post-turn work inside its
originating Run.

## Stable-head Session fork

`/fork` requires the selected source Session to be idle. It creates a new independent Session from the source's last stable committed head:

- Agents SDK continuation items are copied through the SDK's public Session API;
- the JSONL replay journal is cloned byte-for-byte when it exists;
- a non-empty title receives the suffix ` (fork)`;
- `branch_from_session_id` records the direct source Session;
- selection changes to the target only after every persistence step succeeds.

An empty or older no-journal Session can still be forked. A compacted source stays compacted because continuation is copied from the SDK Session rather than reconstructed from replay history. Failure leaves the source unchanged and rolls back the target across SDK state, journal, and metadata; incomplete rollback is reported explicitly.

The target does not inherit active Runs, Run options, pending Steer entries, queued
follow-ups, queue pause state, Todos, pending approvals, archive state, background
processes, usage counters, context-occupancy caches, or UI state. It is always created
unarchived. Source and target evolve independently after the snapshot. Forking a fork
stores its immediate parent rather than flattening ancestry; archiving a parent does
not remove that parent from an existing child's lineage.

This operation is not conversation branching: it cannot select or edit a historical turn and does not use the SDK's turn-branching APIs. There is no branch tree UI in this version.

## Output and replay

`OutputEvent` remains independent of Session and UI concerns. Runtime wraps it in `RuntimeOutput(run_id, session_id, event)`.

The application handles each output in this order:

1. append it to the originating Session's JSONL journal;
2. obtain the committed journal sequence;
3. offer it to the presentation layer.

The Agents SDK SQLite history is model continuation state and may be replaced by compaction. The JSONL Session journal is the append-only user-visible replay source and is not compacted. Steer is recorded as `user_steer`; Queue input is absent until promotion and is then recorded once as `user_message`.

Inactive Session output is not rendered into the selected timeline, but it is still journaled. When the user selects that Session, the TUI reloads its journal. During reload, live output and approval updates are buffered and deduplicated by journal sequence so the final timeline neither loses nor duplicates records.

The existing TUI remains a full-history reference client. A separate
`read_session_journal_page()` contract supports future bounded responses: records are
raw append-order records returned in ascending sequence order, `before_seq` is an
exclusive upper bound, and `has_older` indicates another page. Returned record memory
is bounded by `limit`, although the first implementation still validates the complete
file and therefore remains O(file size) in read time. Page boundaries may split a
tool call from its output or another semantic span. Clients reconcile adjacent pages
and live events by the original journal sequence; pagination never rewrites,
duplicates, or synthesizes sequence numbers.

Activity labels are derived annotations. Nearby reasoning is isolated by `run_id`; a Tool call can still receive and persist its label while its Session is not selected.

## Approval

Every `ApprovalRequest` carries its immutable Runtime-generated `approval_id`, Run and
Session identity, plus the captured permission mode. The TUI resolves the exact
pending Future by `approval_id`, not Python object identity. New approval journal
records include both `approval_id` and `run_id`; older records without those fields
remain readable by the existing history projection.

The TUI owns one global FIFO of pending approvals and displays its head in a dedicated
Approval panel with Session attribution. The panel does not replace or disable the
normal Composer. Multiple Runs may wait simultaneously: while Session A waits, the
user can select Session B and start or continue B; selecting A still permits Steer,
Queue, and Stop according to A's frozen interaction mode.

Resolving the panel head completes only its matching Future. A decision is appended to
the originating Session journal before the timeline is updated. Permission Agent usage
is returned through `ApprovalOutcome` and included in the originating Run's auxiliary
usage.

Permission behavior remains:

- `manual`: always ask the user;
- `review`: auto-approve or auto-reject when the Permission Agent decides, otherwise ask the user;
- Permission Agent failure: fall back to a manual request.

## Archive lifecycle

Archive is persistent Session metadata. Startup performs an idempotent schema migration
for `archived_at`. Normal Session listings exclude archived records, while
`/unarchive` queries them explicitly.

`/archive` rejects an active Session and a Session with queued intent. Archiving does
not delete SDK continuation state, journal history, title, or lineage. The newly
archived Session remains selected and visible with an Archived marker so the command
does not silently create or select another Session. It is read-only until restored or
the user switches away; after switching away it disappears from the normal sidebar.

Unarchive clears `archived_at`, selects the restored Session, and makes normal
operations available again. Fork reads a stable source without mutating it, so an
archived parent may be used as lineage; the target itself remains unarchived.

## TUI interaction projection

The TUI combines transient Runtime snapshots, persistent Session metadata, and the
AppConfig future default at the presentation boundary. It does not introduce another
domain state store.

The primary status uses this priority:

```text
Waiting approval
> Stopping
> Running
> Queue paused
> Queued
> Ready
```

The active status shows the Run's frozen `Follow-ups: Steer` or `Follow-ups: Queue`
mode. Ready shows the future default. Queue and pending-Steer counts are supplemental
labels and do not override the primary status. Archived is an independent lifecycle
badge; archive rules prevent it from conflicting with active or queued work. Sidebar
lineage uses the direct parent Session ID and does not build a branch tree.

Runtime state notifications are attributed by Session, including when that Session is
not selected. Selection changes only which timeline and controls are displayed.

## Plain console boundary

The plain console preserves sequential command, Run, output, approval, archive, and
resume behavior, but it does not provide the TUI's concurrent interaction surface. It
awaits the active Run before reading the next command, so the user cannot submit live
Steer or Queue input, issue `/stop` during that Run, switch Sessions while it runs, or
operate Todo, Queue, and Approval panels. `/followup` can still change the default for
a future Run, but that does not make live follow-up submission available in plain
mode. Manual approval remains an inline console prompt.

## Usage

Main and auxiliary usage are stored per Session. Auxiliary usage includes title generation, permission review, Activity labels, and auto compaction as applicable. Run completion always updates the Run's Session, regardless of the Session currently selected.

Successful manual or automatic compaction invalidates only the corresponding Session's cached Main context occupancy.

## Background processes

`ExecutionState` is application-global. Managed background processes can outlive the Run that created them and are not cancelled by Session selection changes or Run completion.

Each `BackgroundJob` records `owner_session_id` and `owner_run_id` for attribution. `/ps` lists every managed process and shows its owner Session; it is not filtered to the selected Session.

## Shutdown

Normal UI exit is blocked while Runs are active or any Session retains queued
follow-ups. The user must finish or stop Runs and clear retained queues first.
Defensive shutdown still calls `AgentRuntime.aclose()`:

1. cancel remaining Run tasks;
2. await them with exception collection;
3. clear the registries after Run `finally` blocks close Session handles;
4. close the shared execution environment and sandbox;
5. leave the application-scoped MCP manager.

Run failure and cancellation use the same cleanup path and cannot leave a Session permanently busy. Defensive shutdown discards transient Runtime queues after Run cleanup; it does not write unpromoted Queue items into conversation history.

## Current limits

All Sessions share one workspace, sandbox, MCP manager, and background-process registry. Concurrent Runs may therefore edit the same workspace paths. This version intentionally does not provide filesystem locking or per-Session workspaces.

It also does not persist active Runs, pending Steer entries, Queued inputs, queue pause
state, Todos, or pending approvals across process restarts. It does not implement
historical-turn conversation branching, WebUI transport, an actor system, or a
distributed scheduler.
