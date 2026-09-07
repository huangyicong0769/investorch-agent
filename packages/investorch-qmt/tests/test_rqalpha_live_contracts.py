from decimal import Decimal

import pytest

from investorch_qmt.rqalpha_live.contracts import RQAlphaLiveBootstrapSnapshot


def snapshot_wire():
    return {
        "schema_version": 1,
        "deployment_id": "deployment-1",
        "portfolio_id": "portfolio-1",
        "broker_account_id": "account-1",
        "ledger_sequence": 12,
        "generated_at": "2026-09-07T00:00:00+00:00",
        "base_currency": "CNY",
        "cash": "100000.00",
        "positions": [{"code": "600519", "market": "XSHG", "quantity": "100"}],
    }


def test_accepts_exact_v1_decimal_strings():
    snapshot = RQAlphaLiveBootstrapSnapshot.from_wire(snapshot_wire())
    assert snapshot.cash == Decimal("100000.00")
    assert snapshot.positions[0].quantity == Decimal("100")
    assert snapshot.ledger_sequence == 12


@pytest.mark.parametrize("version", [None, 0, 2, "1", True, 1.0])
def test_rejects_any_version_except_exact_integer_one(version):
    wire = snapshot_wire()
    wire["schema_version"] = version
    with pytest.raises(ValueError):
        RQAlphaLiveBootstrapSnapshot.from_wire(wire)


@pytest.mark.parametrize("field", list(snapshot_wire()))
def test_rejects_missing_contract_fields(field):
    wire = snapshot_wire()
    del wire[field]
    with pytest.raises(ValueError):
        RQAlphaLiveBootstrapSnapshot.from_wire(wire)


@pytest.mark.parametrize("amount", [100.0, 100, True, "NaN", "Infinity", "1_000", " 10", "bad"])
@pytest.mark.parametrize("field", ["cash", "quantity"])
def test_financial_values_require_decimal_strings(field, amount):
    wire = snapshot_wire()
    target = wire if field == "cash" else wire["positions"][0]
    target[field] = amount
    with pytest.raises(ValueError):
        RQAlphaLiveBootstrapSnapshot.from_wire(wire)


def test_rejects_duplicates_and_unknown_shape():
    wire = snapshot_wire()
    wire["positions"].append(dict(wire["positions"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        RQAlphaLiveBootstrapSnapshot.from_wire(wire)
    wire = snapshot_wire()
    wire["total_cost"] = "1234"
    with pytest.raises(ValueError, match="exactly"):
        RQAlphaLiveBootstrapSnapshot.from_wire(wire)
    wire = snapshot_wire()
    wire["positions"][0]["avg_price"] = "12.34"
    with pytest.raises(ValueError, match="exactly"):
        RQAlphaLiveBootstrapSnapshot.from_wire(wire)
