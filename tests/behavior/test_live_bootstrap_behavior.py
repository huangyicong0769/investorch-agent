from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal

import pytest

from investorch.live.domain import LiveExecutionError
from investorch.portfolio.bootstrap import build_bootstrap_snapshot
from investorch.portfolio.domain import InstrumentId, OpeningPosition
from tests.behavior.test_live_deployment_behavior import setup_live


async def test_bootstrap_exports_exact_account_state_and_ledger_head(tmp_path):
    config, portfolios, portfolio, live = await setup_live(tmp_path)
    await portfolios.initialize(
        portfolio.id,
        cash=Decimal("12345.6700"),
        positions=[
            OpeningPosition(InstrumentId("600519", "XSHG"), Decimal("1E+2"), Decimal("99999")),
            OpeningPosition(InstrumentId("000001", "XSHE"), Decimal("12.50"), Decimal("500")),
        ],
        source="opening",
    )
    await portfolios.assign_unallocated_assets(portfolio.id, "account")
    prepared = await live.prepare_deployment(portfolio.id, "account")
    await live.activate_deployment(prepared.deployment_id)
    snapshot = build_bootstrap_snapshot(config.portfolio_db, prepared.deployment_id)
    wire = snapshot.to_wire()
    assert wire == {
        "schema_version": 1,
        "deployment_id": prepared.deployment_id,
        "portfolio_id": portfolio.id,
        "broker_account_id": "account",
        "ledger_sequence": len(await portfolios.list_ledger(portfolio.id)),
        "generated_at": snapshot.generated_at.isoformat(),
        "base_currency": "CNY",
        "cash": "12345.6700",
        "positions": [
            {"code": "000001", "market": "XSHE", "quantity": "12.50"},
            {"code": "600519", "market": "XSHG", "quantity": "100"},
        ],
    }
    assert datetime.fromisoformat(wire["generated_at"]).utcoffset() is not None
    wire["positions"][0]["quantity"] = "999"
    assert snapshot.to_wire()["positions"][0]["quantity"] == "12.50"
    with pytest.raises(FrozenInstanceError):
        snapshot.cash = Decimal(0)


@pytest.mark.parametrize("status", ["PREPARED", "STOPPED", "FAILED"])
async def test_bootstrap_requires_active_deployment(tmp_path, status):
    config, _portfolios, portfolio, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(portfolio.id, "account")
    if status == "STOPPED":
        await live.activate_deployment(deployment.deployment_id)
        await live.stop_deployment(deployment.deployment_id)
    elif status == "FAILED":
        await live.fail_deployment(deployment.deployment_id, "stopped")
    with pytest.raises(LiveExecutionError, match="ACTIVE"):
        build_bootstrap_snapshot(config.portfolio_db, deployment.deployment_id)


@pytest.mark.parametrize("account_id", [None, "other"])
async def test_assets_outside_selected_account_prevent_activation_and_snapshot(tmp_path, account_id):
    from datetime import UTC

    from investorch.portfolio.domain import BrokerAccount
    from investorch.portfolio.storage import create_broker_account

    config, portfolios, portfolio, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(portfolio.id, "account")
    if account_id is not None:
        now = datetime.now(UTC)
        create_broker_account(
            config.portfolio_db, BrokerAccount(account_id, "broker", "external-other", "Other", "stock", now, now)
        )
    await portfolios.record_cash_flow(portfolio.id, amount=Decimal("1.00"), source="manual")
    if account_id is not None:
        await portfolios.assign_unallocated_assets(portfolio.id, account_id)
    with pytest.raises(LiveExecutionError, match="multi-account / unallocated"):
        await live.activate_deployment(deployment.deployment_id)
    with pytest.raises(LiveExecutionError, match="ACTIVE"):
        build_bootstrap_snapshot(config.portfolio_db, deployment.deployment_id)


async def test_empty_account_bootstrap_is_zero_and_missing_deployment_is_rejected(tmp_path):
    config, _portfolios, portfolio, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(portfolio.id, "account")
    await live.activate_deployment(deployment.deployment_id)
    wire = build_bootstrap_snapshot(config.portfolio_db, deployment.deployment_id).to_wire()
    assert (wire["cash"], wire["positions"], wire["ledger_sequence"]) == ("0", [], 0)
    with pytest.raises(LiveExecutionError, match="not found"):
        build_bootstrap_snapshot(config.portfolio_db, "missing")
