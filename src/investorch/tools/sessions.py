from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal, Protocol

from agents import FunctionTool
from pydantic import StrictBool, StrictInt

from investorch.tools.errors import tool


class ListSessionsQuery(Protocol):
    def __call__(
        self, *, query: str | None, include_archived: bool, limit: int, offset: int
    ) -> Awaitable[dict[str, object]]: ...


class ReadSessionQuery(Protocol):
    def __call__(
        self,
        *,
        session_id: str,
        view: Literal["conversation", "events"],
        before_seq: int | None,
        snapshot_seq: int | None,
        limit: int,
    ) -> Awaitable[dict[str, object]]: ...


def make_session_tools(*, list_query: ListSessionsQuery, read_query: ReadSessionQuery) -> list[FunctionTool]:
    """Build read-only Tools from Application queries already bound to this Host's dependencies."""

    @tool
    async def list_sessions(
        query: str | None = None,
        include_archived: StrictBool = False,
        limit: StrictInt = 50,
        offset: StrictInt = 0,
    ) -> dict[str, object]:
        """List current Root Sessions with title-only filtering, current status, and offset pagination.

        Args:
            query: Unicode case-insensitive title substring; null or blank disables filtering.
                Searches titles only, not Session IDs or message contents.
            include_archived: Include both archived and unarchived Sessions when true. Defaults to false.
            limit: Maximum Session rows to return, from 1 through 100. Defaults to 50.
                The response budget may shorten the page.
            offset: Non-negative number of matching Sessions to skip. Defaults to 0.
                Continue with the returned next_offset; pages reflect the current listing without a frozen snapshot.
        """
        return await list_query(query=query, include_archived=include_archived, limit=limit, offset=offset)

    @tool
    async def read_session(
        session_id: str,
        view: Literal["conversation", "events"] = "conversation",
        before_seq: StrictInt | None = None,
        snapshot_seq: StrictInt | None = None,
        limit: StrictInt = 50,
    ) -> dict[str, object]:
        """Read exact-ID history, including self/archived; page backwards with returned snapshot_seq and next_before_seq.

        Conversation returns effective user/assistant/tool records and image metadata. Oversized content has explicit
        omitted_fields. Events preserve raw values and fail if over budget; reduce limit or use conversation.

        Args:
            session_id: Exact existing Session ID in the current Root; prefixes are not accepted.
                Self, other running Sessions, and archived Sessions are readable.
            view: "conversation" for visible user/assistant/tool records, or "events" for original Journal events.
                Defaults to "conversation".
            before_seq: Exclusive positive source Journal sequence bound, or null for the newest page.
                For older pages, pass the returned next_before_seq; must not exceed snapshot_seq + 1.
            snapshot_seq: Frozen Journal head returned by the first read; null captures the current committed head.
                Reuse the same value with before_seq on later pages. Zero represents an empty snapshot.
            limit: Maximum visible Conversation records or raw Events to return, from 1 through 100. Defaults to 50.
                The response budget may shorten Conversation pages; oversized Events return an explicit error.
        """
        return await read_query(
            session_id=session_id, view=view, before_seq=before_seq, snapshot_seq=snapshot_seq, limit=limit
        )

    return [list_sessions, read_session]
