from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from qmt_agent.storage import SessionRecord


class SessionListItem(ListItem):
    def __init__(
        self,
        record: SessionRecord,
        *,
        current: bool = False,
        running: bool = False,
    ) -> None:
        self.record = record
        self.session_id = record.session_id
        title = record.title or "(untitled)"
        session_status = (
            f"{record.session_id[:8]} · Archived"
            if record.archived_at is not None
            else record.session_id[:8]
        )
        classes = "current-session" if current else None
        super().__init__(
            Vertical(
                Label(f"● {title}" if running else title, classes="session-title"),
                Label(session_status, classes="session-id"),
                classes="session-item-content",
            ),
            classes=classes,
        )


class SessionSidebar(ListView):
    async def replace_sessions(
        self,
        records: list[SessionRecord],
        current_session_id: str,
        active_session_ids: set[str],
    ) -> None:
        await self.clear()
        current_index: int | None = None
        session_ids = {record.session_id for record in records}

        if current_session_id not in session_ids:
            records = [
                SessionRecord(
                    session_id=current_session_id,
                    title=None,
                    branch_from_session_id=None,
                    archived_at=None,
                    created_at="",
                    updated_at="",
                ),
                *records,
            ]

        for index, record in enumerate(records):
            current = record.session_id == current_session_id
            await self.append(
                SessionListItem(
                    record,
                    current=current,
                    running=record.session_id in active_session_ids,
                )
            )
            if current:
                current_index = index

        self.index = current_index
