"""Keep durable history intact while making prior-Run input portable between model connections."""

import json
from collections import Counter

from agents import TResponseInputItem
from agents.run_config import CallModelData, CallModelInputFilter, ModelInputData


def is_deepseek_web_call(item: TResponseInputItem) -> bool:
    return isinstance(item, dict) and item.get("type") == "web_search_call" and "kind" in item


def latest_deepseek_web_round(items: list[TResponseInputItem]) -> tuple[int, int] | None:
    """Find the newest user round with hosted web output and its restoring input."""
    last = None
    start = 0
    has_web = False
    for index, item in enumerate(items):
        if item.get("role") == "user":
            if has_web:
                last = (start, index)
            start, has_web = index, False
        elif is_deepseek_web_call(item):
            has_web = True
    return (start, len(items)) if has_web else last


def portable_history_filter(history: list[TResponseInputItem], *, deepseek: bool = False) -> CallModelInputFilter:
    """Keep signed web items intact for DeepSeek and portable history for the other providers."""
    previous = Counter(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in history)

    def filter_input(data: CallModelData) -> ModelInputData:
        remaining = previous.copy()
        items: list[TResponseInputItem] = []
        for item in data.model_data.input:
            fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if remaining[fingerprint]:
                remaining[fingerprint] -= 1
                if isinstance(item, dict):
                    if is_deepseek_web_call(item):
                        if deepseek:
                            items.append(item)
                        continue
                    if item.get("type") == "reasoning":
                        continue
                    item = {key: value for key, value in item.items() if key != "id"}
            items.append(item)
        return ModelInputData(input=items, instructions=data.model_data.instructions)

    return filter_input
