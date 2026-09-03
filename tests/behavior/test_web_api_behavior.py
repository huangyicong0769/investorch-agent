from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from investorch.portfolio import CashFlow, InstrumentId, OpeningPosition
from tests.support.web import open_test_web


@pytest.mark.asyncio
async def test_health_exposes_ready_status(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        response = await web.client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_bootstrap_stays_sessionless_until_user_creates_session(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        response = await web.client.get("/api/bootstrap")

        assert response.status_code == 200
        payload = response.json()
        assert payload["initial_session_id"] is None
        assert payload["runtime"] is None
        assert payload["presentation"] is None
        assert payload["sessions"] == []


@pytest.mark.asyncio
async def test_portfolio_index_exposes_current_active_and_archived_summaries(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        active = await web.host.portfolios.create(
            name="Core",
            base_currency="CNY",
            description="Long-term",
        )
        await web.host.portfolios.initialize(
            active.id,
            cash=Decimal("10000.0300"),
            positions=(
                OpeningPosition(InstrumentId("600519", "XSHG"), Decimal("0.10"), Decimal("152.3450")),
                OpeningPosition(InstrumentId("000001", "XSHE"), Decimal("2"), None),
            ),
            source="test",
        )
        archived = await web.host.portfolios.create(name="Old", base_currency="USD")
        await web.host.portfolios.archive(archived.id)

        response = await web.client.get("/api/portfolios")

        assert response.status_code == 200
        by_id = {item["portfolio_id"]: item for item in response.json()["portfolios"]}
        assert by_id == {
            active.id: {
                "portfolio_id": active.id,
                "name": "Core",
                "description": "Long-term",
                "status": "ACTIVE",
                "base_currency": "CNY",
                "logical_cash": {"CNY": "10000.0300"},
                "holdings_count": 2,
                "strategy_binding": None,
            },
            archived.id: {
                "portfolio_id": archived.id,
                "name": "Old",
                "description": None,
                "status": "ARCHIVED",
                "base_currency": "USD",
                "logical_cash": {},
                "holdings_count": 0,
                "strategy_binding": None,
            },
        }


@pytest.mark.asyncio
async def test_portfolio_detail_preserves_exact_unknown_cost_and_maps_missing_portfolio(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        portfolio = await web.host.portfolios.create(name="Core", base_currency="CNY")
        await web.host.portfolios.initialize(
            portfolio.id,
            positions=(OpeningPosition(InstrumentId("000001", "XSHE"), Decimal("0.10"), None),),
            source="test",
        )
        await web.host.portfolios.archive(portfolio.id)

        response = await web.client.get(f"/api/portfolios/{portfolio.id}")
        missing = await web.client.get("/api/portfolios/missing")
        write_attempts = (
            await web.client.post("/api/portfolios", json={"name": "Not allowed"}),
            await web.client.patch(f"/api/portfolios/{portfolio.id}", json={"name": "Not allowed"}),
            await web.client.delete(f"/api/portfolios/{portfolio.id}"),
        )

        assert response.status_code == 200
        assert response.json()["state"] == {
            "portfolio_id": portfolio.id,
            "cash": {},
            "holdings": [
                {
                    "instrument": {"code": "000001", "market": "XSHE"},
                    "quantity": "0.10",
                    "total_cost": None,
                    "average_cost": None,
                }
            ],
        }
        assert response.json()["portfolio"]["name"] == "Core"
        assert response.json()["portfolio"]["status"] == "ARCHIVED"
        assert "created_at" in response.json()["portfolio"]
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "portfolio_not_found"
        assert all(write_attempt.status_code == 405 for write_attempt in write_attempts)


@pytest.mark.asyncio
async def test_portfolio_ledger_returns_recent_fifty_in_audit_order_with_void_payload(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        portfolio = await web.host.portfolios.create(name="Core", base_currency="CNY")
        original = await web.host.portfolios.record_cash_flow(
            portfolio.id,
            amount=Decimal("1"),
            source="test",
        )
        for amount in range(2, 52):
            await web.host.portfolios.record_cash_flow(
                portfolio.id,
                amount=Decimal(amount),
                source="test",
            )
        await web.host.portfolios.correct_entry(
            portfolio.id,
            target_entry_id=original.entries[0].entry_id,
            replacement_payload=CashFlow("CNY", Decimal("1.5")),
            reason="correct imported amount",
            source="test",
        )

        response = await web.client.get(f"/api/portfolios/{portfolio.id}/ledger")
        missing = await web.client.get("/api/portfolios/missing/ledger")
        too_small = await web.client.get(f"/api/portfolios/{portfolio.id}/ledger?limit=0")
        too_large = await web.client.get(f"/api/portfolios/{portfolio.id}/ledger?limit=201")

        assert response.status_code == 200
        payload = response.json()
        assert payload["portfolio_id"] == portfolio.id
        assert payload["returned"] == 50
        assert payload["total"] == 53
        assert payload["has_older"] is True
        assert [entry["sequence"] for entry in payload["entries"]] == list(range(4, 54))
        assert payload["entries"][-2]["entry_type"] == "VOID"
        assert payload["entries"][-2]["payload"] == {
            "target_entry_id": original.entries[0].entry_id,
            "reason": "correct imported amount",
        }
        assert payload["entries"][-1]["payload"] == {"currency": "CNY", "amount": "1.5"}
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "portfolio_not_found"
        assert too_small.status_code == 422
        assert too_large.status_code == 422


@pytest.mark.asyncio
async def test_session_related_portfolios_resolve_current_summaries_in_relation_order(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]
        first = await web.host.portfolios.create(name="First", base_currency="USD")
        second = await web.host.portfolios.create(name="Second", base_currency="CNY")
        await web.host.portfolios.archive(first.id)

        await web.client.get("/api/portfolios")
        await web.client.get(f"/api/portfolios/{first.id}")
        unrelated = await web.client.get(f"/api/sessions/{session_id}/related-portfolios")
        await web.host.sessions.add_related_portfolio_ids(session_id, (first.id, "unavailable", second.id))

        response = await web.client.get(f"/api/sessions/{session_id}/related-portfolios")

        assert unrelated.status_code == 200
        assert unrelated.json() == {"portfolios": []}
        assert response.status_code == 200
        assert [item["portfolio_id"] for item in response.json()["portfolios"]] == [first.id, second.id]
        assert [item["status"] for item in response.json()["portfolios"]] == ["ARCHIVED", "ACTIVE"]
        assert all(item["holdings_count"] == 0 for item in response.json()["portfolios"])


@pytest.mark.asyncio
async def test_post_sessions_explicitly_creates_a_readable_session(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]

        fetched = await web.client.get(f"/api/sessions/{session_id}")
        bootstrap = await web.client.get("/api/bootstrap")

        assert created.status_code == 200
        assert fetched.status_code == 200
        assert fetched.json()["session"]["session_id"] == session_id
        assert [session["session_id"] for session in bootstrap.json()["sessions"]] == [session_id]


@pytest.mark.asyncio
async def test_deleting_created_session_returns_to_sessionless_without_replacement(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        initial = await web.client.get("/api/bootstrap")
        assert initial.status_code == 200
        assert initial.json()["initial_session_id"] is None
        assert initial.json()["runtime"] is None
        assert initial.json()["presentation"] is None
        assert initial.json()["sessions"] == []

        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]
        assert (await web.client.get(f"/api/sessions/{session_id}")).status_code == 200

        deleted = await web.client.request("DELETE", f"/api/sessions/{session_id}", json={"confirm": True})
        bootstrap = await web.client.get("/api/bootstrap")
        missing = await web.client.get(f"/api/sessions/{session_id}")

        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert deleted.json()["session_id"] == session_id
        assert deleted.json()["replacement_session_id"] is None
        assert bootstrap.json()["initial_session_id"] is None
        assert bootstrap.json()["runtime"] is None
        assert bootstrap.json()["presentation"] is None
        assert bootstrap.json()["sessions"] == []
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path_suffix"),
    [
        ("DELETE", ""),
        ("POST", "/clear"),
        ("DELETE", "/queue"),
    ],
)
async def test_destructive_operations_require_explicit_confirmation(
    tmp_path: Path,
    method: str,
    path_suffix: str,
) -> None:
    async with open_test_web(tmp_path) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]

        response = await web.client.request(
            method,
            f"/api/sessions/{session_id}{path_suffix}",
            json={"confirm": False},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "confirmation_required"
        assert (await web.client.get(f"/api/sessions/{session_id}")).status_code == 200


@pytest.mark.asyncio
async def test_unknown_session_has_stable_not_found_error(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        response = await web.client.get("/api/sessions/unknown")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_empty_message_is_invalid_and_does_not_start_run(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]

        response = await web.client.post(f"/api/sessions/{session_id}/messages", json={"text": "  "})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_message"
        assert web.runtime.runtime.session_snapshot(session_id).run_id is None


@pytest.mark.asyncio
async def test_session_without_journal_has_empty_history_page(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]

        response = await web.client.get(f"/api/sessions/{session_id}/history")

        assert response.status_code == 200
        assert response.json()["records"] == []
        assert response.json()["has_older"] is False


@pytest.mark.asyncio
async def test_history_default_page_size_comes_from_appconfig(tmp_path: Path) -> None:
    async with open_test_web(tmp_path, {"web": {"history_page_size": 1}}) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]
        await web.runtime.journal.record_user_message(session_id, "one")
        await web.runtime.journal.record_user_message(session_id, "two")

        response = await web.client.get(f"/api/sessions/{session_id}/history")

        assert response.status_code == 200
        assert [record["text"] for record in response.json()["records"]] == ["two"]
        assert response.json()["has_older"] is True


@pytest.mark.asyncio
async def test_corrupt_history_maps_to_machine_readable_journal_error(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]
        (web.runtime.config.session_journal_dir / f"{session_id}.jsonl").write_text("not-json\n", encoding="utf-8")

        response = await web.client.get(f"/api/sessions/{session_id}/history")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "journal_invalid"


@pytest.mark.asyncio
async def test_future_default_changes_without_mutating_active_run_snapshot(tmp_path: Path) -> None:
    async with open_test_web(tmp_path, {"interaction": {"follow_up_behavior": "steer"}}) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]
        started = await web.client.post(f"/api/sessions/{session_id}/messages", json={"text": "first"})
        await web.runtime.agent_loop.wait_until_started(session_id)

        updated = await web.client.patch("/api/defaults", json={"follow_up_behavior": "queue"})
        defaults = await web.client.get("/api/defaults")
        state = await web.client.get(f"/api/sessions/{session_id}/state")
        follow_up = await web.client.post(f"/api/sessions/{session_id}/messages", json={"text": "more"})

        assert started.json()["disposition"] == "run_started"
        assert updated.json()["follow_up_behavior"] == "queue"
        assert defaults.json()["follow_up_behavior"] == "queue"
        assert state.json()["runtime"]["active_follow_up_behavior"] == "steer"
        assert follow_up.json()["disposition"] == "steer_submitted"

        stopped = await web.client.post(f"/api/sessions/{session_id}/stop")
        assert stopped.json()["status"] == "stopping"
        await web.runtime.wait_for_run_ended(session_id)

        next_run = await web.client.post(f"/api/sessions/{session_id}/messages", json={"text": "next"})
        await web.runtime.agent_loop.wait_until_started(session_id, occurrence=2)
        next_state = await web.client.get(f"/api/sessions/{session_id}/state")
        assert next_run.json()["disposition"] == "run_started"
        assert next_state.json()["runtime"]["active_follow_up_behavior"] == "queue"

        web.runtime.agent_loop.complete(session_id)
        await web.runtime.wait_for_run_ended(session_id, occurrence=2)


@pytest.mark.asyncio
async def test_queue_endpoints_distinguish_missing_and_unpaused_queue(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        created = await web.client.post("/api/sessions")
        session_id = created.json()["session"]["session_id"]

        missing_resume = await web.client.post(f"/api/sessions/{session_id}/queue/resume")
        missing_remove = await web.client.delete(f"/api/sessions/{session_id}/queue/unknown")

        assert missing_resume.status_code == 404
        assert missing_resume.json()["error"]["code"] == "queue_not_found"
        assert missing_remove.status_code == 404
        assert missing_remove.json()["error"]["code"] == "queue_not_found"
