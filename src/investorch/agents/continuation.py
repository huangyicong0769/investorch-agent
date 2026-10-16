"""Keep durable history intact while making prior-Run input portable between model connections."""

import json
from collections import Counter

from agents import TResponseInputItem
from agents.run_config import CallModelData, CallModelInputFilter, ModelInputData


def portable_history_filter(history: list[TResponseInputItem]) -> CallModelInputFilter:
    """Omit prior-Run opaque reasoning and response IDs from requests, never from storage."""
    previous = Counter(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in history)

    def filter_input(data: CallModelData) -> ModelInputData:
        remaining = previous.copy()
        items: list[TResponseInputItem] = []
        for item in data.model_data.input:
            fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if remaining[fingerprint]:
                remaining[fingerprint] -= 1
                if isinstance(item, dict):
                    if item.get("type") == "reasoning":
                        continue
                    item = {key: value for key, value in item.items() if key != "id"}
            items.append(item)
        return ModelInputData(input=items, instructions=data.model_data.instructions)

    return filter_input
