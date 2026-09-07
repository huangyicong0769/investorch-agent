"""Map a clean pre-session snapshot onto RQAlpha's native constructor inputs."""

import json
import math
from collections.abc import Mapping

from .contracts import RQAlphaLiveBootstrapSnapshot


def native_portfolio_inputs(snapshot: RQAlphaLiveBootstrapSnapshot) -> tuple[dict[str, float], list[tuple[str, int]]]:
    # RQAlpha Account holds CNY cash. Other currencies need a real adapter, never relabeling.
    if snapshot.base_currency != "CNY":
        raise ValueError("native RQAlpha bootstrap requires CNY cash")
    cash = float(snapshot.cash)
    if not math.isfinite(cash):
        raise ValueError("cash exceeds native RQAlpha numeric range")
    positions = []
    for position in snapshot.positions:
        if position.market not in {"XSHG", "XSHE"} or not position.code.isascii() or not position.code.isdigit():
            raise ValueError("holding has no supported native RQAlpha instrument mapping")
        if position.quantity < 0 or position.quantity != position.quantity.to_integral_value():
            raise ValueError("native stock bootstrap requires nonnegative whole-share quantities")
        if position.quantity:
            positions.append((f"{position.code}.{position.market}", int(position.quantity)))
    return {"STOCK": cash}, positions


def build_live_config(snapshot_wire: Mapping[str, object], parameters: dict | None = None) -> dict:
    """Build RQAlpha overrides; caller supplies session dates and future real backends."""
    snapshot = RQAlphaLiveBootstrapSnapshot.from_wire(snapshot_wire)
    native_portfolio_inputs(snapshot)
    params = {} if parameters is None else parameters
    try:
        _validate_json(params)
        if not isinstance(params, dict):
            raise ValueError("parameters must be a JSON object")
        context_vars = json.dumps({"investorch_parameters": params}, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("parameters must be a finite JSON object") from exc
    return {
        "base": {"run_type": "r"},
        "extra": {"context_vars": context_vars},
        "mod": {
            "sys_simulation": {"enabled": False},
            "sys_accounts": {"enabled": True},
            "investorch_live": {
                "enabled": True,
                "lib": "investorch_qmt.rqalpha_live.mod",
                "snapshot_json": json.dumps(dict(snapshot_wire), allow_nan=False),
            },
        },
    }


def _validate_json(value: object) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("parameter keys must be strings")
            _validate_json(item)
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    raise ValueError("parameters must contain only JSON values")
