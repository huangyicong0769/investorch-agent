from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StrategySource:
    path: Path
    relative_path: str
    source: bytes
    sha256: str


def load_strategy_source(workspace: Path, strategy_path: str) -> StrategySource:
    """Read and identify one Python strategy confined to the Workspace."""
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
    source = resolved.read_bytes()
    return StrategySource(resolved, resolved.relative_to(root).as_posix(), source, hashlib.sha256(source).hexdigest())


def copy_strategy_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    """Validate strict JSON values and return a caller-independent parameter tree."""
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise ValueError("strategy_parameters must be a JSON object")
    ancestors: set[int] = set()

    def copy(value: Any) -> Any:
        if value is None or type(value) in (str, bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, (dict, list)):
            if id(value) in ancestors:
                raise ValueError("strategy_parameters must not contain cycles")
            ancestors.add(id(value))
            try:
                if isinstance(value, dict):
                    if any(not isinstance(key, str) for key in value):
                        raise ValueError("strategy_parameters object keys must be strings")
                    return {key: copy(item) for key, item in value.items()}
                return [copy(item) for item in value]
            finally:
                ancestors.remove(id(value))
        raise ValueError("strategy_parameters must contain only finite JSON-compatible values")

    return copy(parameters)
