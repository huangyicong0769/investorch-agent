from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents import RunContextWrapper
from pydantic import StrictBool, StrictInt

from investorch.context import AgentContext
from investorch.tools.errors import tool

if TYPE_CHECKING:
    from investorch.application.session_reads import SessionReadOperations


def _session_reads(ctx: RunContextWrapper[AgentContext]) -> SessionReadOperations:
    if ctx.context.session_reads is None:
        raise ValueError("Session read service is unavailable")
    return ctx.context.session_reads


@tool
async def list_sessions(
    ctx: RunContextWrapper[AgentContext],
    query: str | None = None,
    include_archived: StrictBool = False,
    limit: StrictInt = 50,
    offset: StrictInt = 0,
) -> dict[str, object]:
    """List current Root Sessions with title-only filtering, current status, and offset pagination."""
    return await _session_reads(ctx).list_sessions(
        query=query, include_archived=include_archived, limit=limit, offset=offset
    )


@tool
async def read_session(
    ctx: RunContextWrapper[AgentContext],
    session_id: str,
    view: Literal["conversation", "events"] = "conversation",
    before_seq: StrictInt | None = None,
    snapshot_seq: StrictInt | None = None,
    limit: StrictInt = 50,
) -> dict[str, object]:
    """Read exact-ID history, including self/archived; page backwards with returned snapshot_seq and next_before_seq.

    Conversation returns effective user/assistant/tool records and image metadata. Oversized content has explicit
    omitted_fields. Events preserve raw values and fail if over budget; reduce limit or use conversation.
    """
    return await _session_reads(ctx).read_session(
        session_id=session_id, view=view, before_seq=before_seq, snapshot_seq=snapshot_seq, limit=limit
    )
