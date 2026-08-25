from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def _resolve_strategy_path(workspace: Path, strategy_path: str) -> tuple[Path, str]:
    if not strategy_path.strip():
        raise ValueError("strategy_path cannot be empty")

    relative = Path(strategy_path)
    if relative.is_absolute():
        raise ValueError("strategy_path must be relative to the Workspace")

    root = workspace.expanduser().resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("strategy_path must remain inside the Workspace")
    if not resolved.exists():
        raise FileNotFoundError(f"strategy file not found: {strategy_path}")
    if not resolved.is_file():
        raise ValueError(f"strategy_path is not a regular file: {strategy_path}")
    if resolved.suffix != ".py":
        raise ValueError("strategy_path must reference a .py file")

    return resolved, resolved.relative_to(root).as_posix()


def _parse_date(value: str, name: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use ISO YYYY-MM-DD format") from exc

    if value != parsed.isoformat():
        raise ValueError(f"{name} must use ISO YYYY-MM-DD format")

    return parsed


def _validate_initial_cash(initial_cash: float) -> float:
    if (
        isinstance(initial_cash, bool)
        or not isinstance(initial_cash, (int, float))
        or not math.isfinite(initial_cash)
        or initial_cash <= 0
    ):
        raise ValueError("initial_cash must be a finite number greater than zero")

    return float(initial_cash)


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalize_json(value.value)
    if isinstance(value, Mapping):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]

    item = getattr(value, "item", None)
    if callable(item):
        return _normalize_json(item())

    raise TypeError(f"Value is not JSON serializable: {type(value).__name__}")
