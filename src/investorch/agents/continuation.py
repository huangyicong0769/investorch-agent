"""Keep durable history intact while making prior-Run input portable between model connections."""

import asyncio
from itertools import chain

from agents import SQLiteSession, TResponseInputItem
from agents.memory import await_session_mutation

from investorch.storage.sessions import append_session_items, get_session_history


def is_deepseek_web_call(item: TResponseInputItem) -> bool:
    return isinstance(item, dict) and item.get("type") == "web_search_call" and "kind" in item


def latest_deepseek_web_round(items: list[TResponseInputItem]) -> tuple[int, int] | None:
    """Prefer the newest round that can replay a signed page exactly."""
    last_web_round = last_signed_round = None
    start = 0
    has_web = has_signature = False
    for end, item in enumerate(chain(items, ({"role": "user"},))):
        if item.get("role") == "user":
            if has_web:
                last_web_round = (start, end)
                if has_signature:
                    last_signed_round = last_web_round
            start = end
            has_web = has_signature = False
        elif is_deepseek_web_call(item):
            has_web = True
            has_signature = has_signature or bool(item.get("signature"))
    return last_signed_round or last_web_round


def _portable_item(item: TResponseInputItem, *, deepseek: bool) -> TResponseInputItem | None:
    if not isinstance(item, dict):
        return item
    if item.get("type") == "web_search_call":
        return item if deepseek or not is_deepseek_web_call(item) else None
    if item.get("type") == "reasoning":
        return None
    item.pop("id", None)
    return item


def portable_history(history: list[TResponseInputItem]) -> list[TResponseInputItem]:
    """Copy conversation data for a separate task such as title generation or summarization."""
    return [
        item
        for raw in history
        if (item := _portable_item(raw.copy() if isinstance(raw, dict) else raw, deepseek=False)) is not None
    ]


class MainRunSession:
    """Read replay input and attribute new history to the Run's frozen connection."""

    def __init__(self, session: SQLiteSession, connection: str, *, deepseek: bool) -> None:
        self._session = session
        self._connection = connection
        self._deepseek = deepseek
        self.session_id = session.session_id
        self.session_settings = session.session_settings

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        if limit is not None:
            # The SDK uses bounded reads to reconcile persisted batches.
            return await self._session.get_items(limit)
        history = await asyncio.to_thread(get_session_history, self._session.db_path, self.session_id)
        items: list[TResponseInputItem] = []
        for item, source in history:
            if (
                source != self._connection
                and isinstance(item, dict)
                and ("id" in item or item.get("type") in ("reasoning", "web_search_call"))
            ):
                item = _portable_item(item, deepseek=self._deepseek)
            if item is not None:
                items.append(item)
        return items

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await await_session_mutation(
            asyncio.to_thread(
                append_session_items,
                self._session.db_path,
                self.session_id,
                [(item, self._connection) for item in items],
            )
        )

    async def pop_item(self) -> TResponseInputItem | None:
        return await self._session.pop_item()

    async def clear_session(self) -> None:
        await self._session.clear_session()
