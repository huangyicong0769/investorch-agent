from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from qmt_agent.storage import SessionRecord


class SessionListItem(ListItem):
    def __init__(
        self,
        session_id: str,
        title: str | None,
        *,
        current: bool = False,
        running: bool = False,
    ) -> None:
        self.session_id = session_id
        classes = "current-session" if current else None
        super().__init__(
            Vertical(
                Label(f"● {title or '(untitled)'}" if running else title or "(untitled)", classes="session-title"),
                Label(session_id[:8], classes="session-id"),
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
                SessionRecord(current_session_id, None, "", ""),
                *records,
            ]

        for index, record in enumerate(records):
            current = record.session_id == current_session_id
            await self.append(
                SessionListItem(
                    record.session_id,
                    record.title,
                    current=current,
                    running=record.session_id in active_session_ids,
                )
            )
            if current:
                current_index = index

        self.index = current_index
