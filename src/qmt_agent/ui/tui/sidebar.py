from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from qmt_agent.runtime import RuntimeSessionSnapshot
from qmt_agent.storage import SessionRecord


def format_session_status(snapshot: RuntimeSessionSnapshot | None) -> str:
    if snapshot is None:
        return "Ready"
    if snapshot.run_phase == "waiting_approval":
        status = "Waiting approval"
    elif snapshot.run_phase == "stopping":
        status = "Stopping"
    elif snapshot.run_id is not None:
        status = "Running"
    elif snapshot.queue_paused and snapshot.queued_count:
        status = "Queue paused"
    elif snapshot.queued_count:
        status = "Queued"
    else:
        status = "Ready"

    details: list[str] = []
    if snapshot.active_follow_up_behavior is not None:
        details.append(snapshot.active_follow_up_behavior.title())
    if snapshot.queued_count:
        details.append(f"{snapshot.queued_count} queued")
    if snapshot.pending_steer_count:
        details.append(f"{snapshot.pending_steer_count} steer pending")
    return " · ".join((status, *details))


class SessionListItem(ListItem):
    def __init__(self, record: SessionRecord, *, current: bool = False, snapshot: RuntimeSessionSnapshot | None = None) -> None:
        self.record = record
        self.session_id = record.session_id
        title = record.title or "(untitled)"
        metadata = [record.session_id[:8]]
        if record.branch_from_session_id:
            metadata.append(f"↳ {record.branch_from_session_id[:8]}")
        if record.archived_at is not None:
            metadata.append("Archived")
        classes = "current-session" if current else None
        super().__init__(
            Vertical(
                Label(title, classes="session-title"),
                Label(format_session_status(snapshot), classes="session-status"),
                Label(" · ".join(metadata), classes="session-id"),
                classes="session-item-content",
            ),
            classes=classes,
        )


class SessionSidebar(ListView):
    async def replace_sessions(self, records: list[SessionRecord], current_session_id: str, snapshots: dict[str, RuntimeSessionSnapshot]) -> None:
        await self.clear()
        current_index: int | None = None
        session_ids = {record.session_id for record in records}

        if current_session_id not in session_ids:
            records = [
                SessionRecord(session_id=current_session_id, title=None, branch_from_session_id=None, archived_at=None, created_at="", updated_at=""),
                *records,
            ]

        for index, record in enumerate(records):
            current = record.session_id == current_session_id
            await self.append(SessionListItem(record, current=current, snapshot=snapshots.get(record.session_id)))
            if current:
                current_index = index

        self.index = current_index
