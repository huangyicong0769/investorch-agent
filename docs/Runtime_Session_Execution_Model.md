# Runtime / Session Execution Model

## Purpose

QMT Agent Trader separates three identities that were previously represented by one mutable session handle:

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
│   └── permission_mode         # future-run default
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

## Run lifecycle

`AgentRuntime.start_run()` synchronously:

1. rejects a second top-level Run for the same Session;
2. generates a unique `run_id`;
3. snapshots reasoning effort and permission mode into `RunOptions`;
4. creates and registers an `asyncio.Task` without awaiting.

The task then:

1. opens the Run-owned `SQLiteSession`;
2. records the user message in the Session journal;
3. invokes the shared, reentrant `AgentLoop` with run-scoped callbacks;
4. closes the Session handle and removes both runtime registry entries in `finally`.

The same Session rejects concurrent top-level Runs instead of queueing them. Different Sessions may run concurrently.

## AgentLoop concurrency

`AgentLoop` stores only stable dependencies. Each invocation receives:

- `run_id` and `session_id`;
- the shared `ExecutionState`;
- the captured reasoning effort;
- run-scoped output and approval callbacks.

The Main Agent is cloned with the captured model settings for every Run. The same clone is reused across all interruption continuations in that Run. The shared Main Agent object is never mutated.

SDK handoffs and agents-as-tools remain inside one top-level Run and share its `AgentContext`, including `session_id`, `run_id`, and `ExecutionState`.

## Session commands

- `/new` creates and selects a new persistent Session identity. Existing Runs continue.
- `/resume` changes only `selected_session_id`.
- `/effort` and `/permission` change defaults for future Runs only.
- `/title` edits metadata for the selected Session.
- `/clear` rejects only when the selected Session is active.
- `/compact` rejects only when the selected Session is active; another idle Session may be compacted while Runs continue elsewhere.
- `/exit` and Ctrl+Q reject normal exit while any Run remains active.
- `/ps` remains global across Sessions.

Manual compaction and a top-level Run are mutually exclusive for the same Session. Auto compaction remains post-turn work inside its originating Run.

## Output and replay

`OutputEvent` remains independent of Session and UI concerns. Runtime wraps it in `RuntimeOutput(run_id, session_id, event)`.

The application handles each output in this order:

1. append it to the originating Session's JSONL journal;
2. obtain the committed journal sequence;
3. offer it to the presentation layer.

The Agents SDK SQLite history is model continuation state and may be replaced by compaction. The JSONL Session journal is the append-only user-visible replay source and is not compacted.

Inactive Session output is not rendered into the selected timeline, but it is still journaled. When the user selects that Session, the TUI reloads its journal. During reload, live output and approval updates are buffered and deduplicated by journal sequence so the final timeline neither loses nor duplicates records.

Activity labels are derived annotations. Nearby reasoning is isolated by `run_id`; a Tool call can still receive and persist its label while its Session is not selected.

## Approval

Every `ApprovalRequest` carries its immutable Run and Session identity plus the captured permission mode.

The TUI owns a FIFO queue of pending approvals and displays one at a time with Session attribution. Multiple Runs may wait simultaneously. A decision is appended to the originating Session journal before the timeline is updated. Permission Agent usage is returned through `ApprovalOutcome` and included in the originating Run's auxiliary usage.

Permission behavior remains:

- `manual`: always ask the user;
- `review`: auto-approve or auto-reject when the Permission Agent decides, otherwise ask the user;
- Permission Agent failure: fall back to a manual request.

## Usage

Main and auxiliary usage are stored per Session. Auxiliary usage includes title generation, permission review, Activity labels, and auto compaction as applicable. Run completion always updates the Run's Session, regardless of the Session currently selected.

Successful manual or automatic compaction invalidates only the corresponding Session's cached Main context occupancy.

## Background processes

`ExecutionState` is application-global. Managed background processes can outlive the Run that created them and are not cancelled by Session selection changes or Run completion.

Each `BackgroundJob` records `owner_session_id` and `owner_run_id` for attribution. `/ps` lists every managed process and shows its owner Session; it is not filtered to the selected Session.

## Shutdown

Normal UI exit is blocked while Runs are active. Defensive shutdown still calls `AgentRuntime.aclose()`:

1. cancel remaining Run tasks;
2. await them with exception collection;
3. clear the registries after Run `finally` blocks close Session handles;
4. close the shared execution environment and sandbox;
5. leave the application-scoped MCP manager.

Run failure and cancellation use the same cleanup path and cannot leave a Session permanently busy.

## Current limits

All Sessions share one workspace, sandbox, MCP manager, and background-process registry. Concurrent Runs may therefore edit the same workspace paths. This version intentionally does not provide filesystem locking or per-Session workspaces.

It also does not implement Run persistence, Run queues, `/fork`, conversation branching, WebUI transport, cancellation commands, an actor system, or a distributed scheduler.
