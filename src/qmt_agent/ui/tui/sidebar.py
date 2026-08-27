from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from qmt_agent.storage import SessionRecord


class SessionListItem(ListItem):
    def __init__(self, session_id: str, title: str | None, *, current: bool = False) -> None:
        self.session_id = session_id
        classes = "current-session" if current else None
        super().__init__(
            Vertical(
                Label(title or "(untitled)", classes="session-title"),
                Label(session_id[:8], classes="session-id"),
            ),
            classes=classes,
        )


class SessionSidebar(ListView):
    async def replace_sessions(
        self,
        records: list[SessionRecord],
        current_session_id: str,
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
            await self.append(SessionListItem(record.session_id, record.title, current=current))
            if current:
                current_index = index

        self.index = current_index
