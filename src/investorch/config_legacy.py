"""One-time compatibility for local configurations written before Python Schema.

These historical model values are never defaults for a new installation.
"""

from typing import Any


def legacy_model_overrides() -> dict[str, Any]:
    models = {}
    for role, effort in (
        ("main", "max"),
        ("title", "none"),
        ("activity", "none"),
        ("permission", "high"),
        ("compact", "high"),
    ):
        model: dict[str, Any] = {
            "name": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "api_key_secret": "DEEPSEEK_API_KEY",
            "reasoning_effort": effort,
        }
        if role in ("main", "compact"):
            model["context_window_tokens"] = 1000000
        models[role] = model
    return {"models": models}
