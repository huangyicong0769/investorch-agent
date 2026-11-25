"""Keep durable history intact while making prior-Run input portable between model connections."""

import json
from collections import Counter
from collections.abc import Callable
from itertools import chain

from agents import TResponseInputItem
from agents.run_config import CallModelData, ModelInputData


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
    return {key: value for key, value in item.items() if key != "id"}


def portable_history(history: list[TResponseInputItem]) -> list[TResponseInputItem]:
    """Copy conversation data for a separate task such as title generation or summarization."""
    return [item for raw in history if (item := _portable_item(raw, deepseek=False)) is not None]


def portable_history_filter(
    history: list[TResponseInputItem], *, deepseek: bool = False
) -> Callable[[CallModelData], ModelInputData]:
    """Normalize selected prior items while keeping new input and Tool call IDs intact."""
    previous = Counter(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in history)

    def filter_input(data: CallModelData) -> ModelInputData:
        remaining = previous.copy()
        items: list[TResponseInputItem] = []
        for item in data.model_data.input:
            fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if remaining[fingerprint]:
                remaining[fingerprint] -= 1
                item = _portable_item(item, deepseek=deepseek)
            if item is not None:
                items.append(item)
        return ModelInputData(input=items, instructions=data.model_data.instructions)

    return filter_input
