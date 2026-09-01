# Runtime / Session Execution Model

[简体中文](Runtime_Session_Execution_Model.zh-CN.md)

## Scope

This document defines the execution and lifecycle contracts implemented in 0.1.0. The higher-level component map is in [Architecture](InvestOrch_Agent_Architecture.md).

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

`presentation.py` converts application values into explicit JSON-safe projection dictionaries.

## Run lifecycle

`AgentRuntime.start_run()` synchronously:

1. rejects a second top-level Run for the same Session;
2. creates a unique Run ID;
3. captures immutable `RunOptions`;
4. registers and starts an `asyncio.Task`.

The task then opens the SDK Session, records input when needed, invokes the reentrant `AgentLoop`, and closes the SDK Session plus Runtime registry entries in `finally`.

Different Sessions may run concurrently. Manual compaction, clear, fork, archive, and a top-level Run are mutually exclusive only for the same Session through a maintenance reservation.

## Future defaults and Run snapshots

Reasoning effort, permission mode, and follow-up behavior are defaults for future Runs. A running `ActiveRun` owns an immutable snapshot. Changing a default does not mutate in-flight behavior.

A queued or steered follow-up captures the future defaults that apply when it is submitted. Later changes do not rewrite those captured options.

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

## Stop and cancellation

Stopping a Run changes its phase to `stopping`, closes follow-up submission, pauses retained Queue intent, and cancels the Run task. Cleanup follows the normal `finally` path. Pending manual approval is cancelled without fabricating an approve/reject decision.

Managed background jobs retain application ownership after their originating Run ends or is cancelled.

## Todo state

Todo is transient Run state projected to clients. It is not stored in SDK continuation, the JSONL journal, Session metadata, or a fork. A new Run begins with an empty Todo snapshot.

## Session lifecycle

- Create makes a new persistent identity.
- Rename updates Session metadata.
- Archive preserves continuation, journal, title, and lineage while making the Session read-only until restored.
- Unarchive restores normal operations.
- Clear removes an idle, unarchived Session without queued intent.
- Compact replaces SDK continuation with a marked assistant summary; the journal remains complete.
- Fork clones the source's last stable committed head into a new independent Session.

Fork copies SDK continuation, the journal when present, title with a suffix, and direct parent lineage. It does not copy active Runs, defaults, Steer/Queue state, Todos, approvals, archive state, jobs, usage, caches, or UI state. It is stable-head cloning, not historical-turn branching.

Empty Sessions that never acquire SDK messages, journal records, metadata, queued intent, or lineage are discarded. Web starts without creating a Session until the user does so; TUI/plain create an initial Session.

## Output and replay

Every semantic `OutputEvent` is wrapped with Run and Session ownership. The application appends it to the originating Session's JSONL journal before projecting it live.

The SDK SQLite Session is model-continuation state and may be compacted. The JSONL journal is append-only user-visible replay state. Inactive Session output is still journaled. Clients reconcile paged history and live events by original journal sequence.

Activity labels are asynchronous derived annotations targeting a Tool-call sequence. Missing labels fall back to the real Tool name and do not change execution truth.

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

Main and auxiliary model usage is accumulated per Session for the current process. Auxiliary usage includes Title, Activity, Permission, and Compact Agents. Displayed Main-context occupancy uses the last physical Main request rather than cumulative Session totals.

Manual or automatic compaction changes only SDK continuation. Failure after a completed answer does not discard that answer, and replacement attempts restore the previous continuation snapshot on error.

## Background jobs

`ExecutionState` owns managed jobs globally. Each job records owner Session and Run IDs for attribution. Job listing is application-global, and jobs may outlive their creator Run.

## Shutdown

Normal client exit is blocked while active Runs or queued follow-ups remain. Defensive shutdown cancels remaining Run tasks, waits for normal cleanup, closes the shared execution environment and MCP manager, and discards unpersisted transient queues.

## Current limits

0.1.0 does not persist active Runs, pending Steer, Queue state, Todo, pending approvals, usage display state, or UI state across process restarts. All Sessions share one workspace and managed-job registry. There is no historical-turn branching, per-Session workspace, distributed scheduler, or cross-process Runtime recovery.
