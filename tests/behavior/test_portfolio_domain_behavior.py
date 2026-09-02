from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investorch.portfolio import (
    InstrumentId,
    LedgerEntry,
    LedgerEntryType,
    OpeningCash,
    OpeningPosition,
    Portfolio,
    PortfolioDomainError,
    PortfolioStatus,
    StrategyBinding,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_instrument_identity_is_provider_neutral() -> None:
    instrument = InstrumentId("600519", "XSHG")

    assert instrument.code == "600519"
    assert instrument.market == "XSHG"


def test_strategy_binding_is_workspace_metadata_only() -> None:
    parameters = {"lookback": 20, "thresholds": [0.1, 0.2]}

    binding = StrategyBinding("strategies/value.py", parameters)
    parameters["lookback"] = 30

    assert binding.source_path == "strategies/value.py"
    assert binding.parameters == {"lookback": 20, "thresholds": [0.1, 0.2]}


def test_portfolio_supports_active_and_archived_metadata() -> None:
    active = Portfolio("p1", "Core", "CNY", NOW, NOW)
    archived = Portfolio("p2", "Archive", "USD", NOW, NOW, status=PortfolioStatus.ARCHIVED)

    assert active.status is PortfolioStatus.ACTIVE
    assert archived.status is PortfolioStatus.ARCHIVED


def test_financial_float_is_rejected() -> None:
    instrument = InstrumentId("600519", "XSHG")

    with pytest.raises(PortfolioDomainError, match="Decimal"):
        OpeningPosition(instrument, 100.0, Decimal("1000"))  # type: ignore[arg-type]


def test_ledger_payload_must_match_entry_type() -> None:
    with pytest.raises(PortfolioDomainError, match="payload does not match"):
        LedgerEntry(
            entry_id="entry-1",
            operation_id="operation-1",
            portfolio_id="portfolio-1",
            sequence=1,
            entry_type=LedgerEntryType.OPENING_POSITION,
            effective_at=NOW,
            recorded_at=NOW,
            source="test",
            payload=OpeningCash("CNY", Decimal("100")),  # type: ignore[arg-type]
        )
