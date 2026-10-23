from __future__ import annotations

import json
import pickle
from pathlib import Path

import h5py

from investorch.tools.quant import inspect_rqalpha_data
from tests.behavior.test_workspace_capabilities import make_tool_context
from tests.support.rqalpha import write_native_bundle


async def test_inspection_connects_a_missing_conversion_target_to_its_predecessors(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path, {"backtest": {"use_cnequity": False}})
    bundle = context.context.config.rqalpha_bundle_dir
    write_native_bundle(bundle)
    original, missing, successor = "000001.XSHE", "000002.XSHE", "000003.XSHE"
    instruments = pickle.loads((bundle / "instruments.pk").read_bytes())
    instruments.append({**instruments[0], "order_book_id": successor})
    (bundle / "instruments.pk").write_bytes(pickle.dumps(instruments))
    with h5py.File(bundle / "stocks.h5", "a") as store:
        store.copy(original, successor)
    records = {
        original: {"successor": missing, "share_conversion_ratio": 1.6, "effective_date": "2024-05-08"},
        missing: {"successor": successor, "share_conversion_ratio": 1.0, "effective_date": "2024-05-10"},
    }
    (bundle / "share_transformation.json").write_text(json.dumps(records))
    before = {path.name: path.read_bytes() for path in bundle.iterdir() if path.is_file()}

    result = await inspect_rqalpha_data.on_invoke_tool(context, json.dumps({"symbols": [missing]}))

    assert isinstance(result, dict)
    assert result["symbols"] == [{"order_book_id": missing, "exists": False}]
    assert result["share_transformations"] == [
        {"predecessor": original, **records[original], "predecessor_exists": True, "successor_exists": False},
        {"predecessor": missing, **records[missing], "predecessor_exists": False, "successor_exists": True},
    ]
    assert before == {path.name: path.read_bytes() for path in bundle.iterdir() if path.is_file()}

    unrelated = await inspect_rqalpha_data.on_invoke_tool(context, json.dumps({"symbols": ["000004.XSHE"]}))
    assert unrelated["share_transformations"] == []

    overview = await inspect_rqalpha_data.on_invoke_tool(context, json.dumps({"symbols": None}))
    assert overview["available"] is True
    assert overview["stock_data"]["instrument_count"] == 2
    assert overview["share_transformations"] == result["share_transformations"]
