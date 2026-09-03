from __future__ import annotations

from decimal import Decimal

import pytest

from investorch.portfolio import (
    CashAdjustment,
    CashFlow,
    CashTransfer,
    Income,
    InstrumentId,
    LedgerEntryType,
    OpeningCash,
    OpeningPosition,
    PortfolioDataError,
    PositionAdjustment,
    PositionTransfer,
    Trade,
    TradeSide,
    TransferDirection,
    Void,
)
from investorch.portfolio.serialization import deserialize_ledger_payload, serialize_ledger_payload

STOCK = InstrumentId("600519", "XSHG")


@pytest.mark.parametrize(
    ("entry_type", "payload"),
    [
        (LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("0.1"), Decimal("0.03"))),
        (LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, Decimal("0.2"), None)),
        (LedgerEntryType.OPENING_CASH, OpeningCash("CNY", Decimal("0.1"))),
        (
            LedgerEntryType.TRADE,
            Trade(STOCK, TradeSide.BUY, Decimal("0.1"), Decimal("0.2"), Decimal("0.03")),
        ),
        (
            LedgerEntryType.TRADE,
            Trade(
                STOCK,
                TradeSide.SELL,
                Decimal("2"),
                Decimal("3"),
                Decimal("0.1"),
                Decimal("0.2"),
                Decimal("0.03"),
            ),
        ),
        (LedgerEntryType.CASH_FLOW, CashFlow("CNY", Decimal("-0.2"))),
        (LedgerEntryType.INCOME, Income("CNY", Decimal("1"), Decimal("0.1"), Decimal("0.03"), STOCK)),
        (LedgerEntryType.INCOME, Income("CNY", Decimal("1"))),
        (
            LedgerEntryType.TRANSFER,
            PositionTransfer(STOCK, TransferDirection.OUT, Decimal("0.2"), Decimal("0.03")),
        ),
        (LedgerEntryType.TRANSFER, CashTransfer("CNY", TransferDirection.IN, Decimal("0.1"))),
        (
            LedgerEntryType.ADJUSTMENT,
            PositionAdjustment(STOCK, Decimal("0.2"), None, "statement correction"),
        ),
        (LedgerEntryType.ADJUSTMENT, CashAdjustment("CNY", Decimal("0.03"), "statement correction")),
        (LedgerEntryType.VOID, Void("entry-old", "wrong amount")),
    ],
)
def test_every_ledger_payload_round_trips_exactly(entry_type: LedgerEntryType, payload: object) -> None:
    encoded = serialize_ledger_payload(payload)

    decoded = deserialize_ledger_payload(entry_type, encoded, entry_id="entry-1")

    assert decoded == payload


def test_union_payloads_store_an_explicit_subtype_discriminator() -> None:
    encoded = serialize_ledger_payload(CashTransfer("CNY", TransferDirection.IN, Decimal("0.1")))

    assert '"kind":"cash"' in encoded


def test_malformed_payload_raises_typed_error_with_entry_context() -> None:
    with pytest.raises(PortfolioDataError, match=r"entry-1.*OPENING_CASH"):
        deserialize_ledger_payload(LedgerEntryType.OPENING_CASH, '{"amount":0.1}', entry_id="entry-1")

    with pytest.raises(PortfolioDataError, match=r"entry-2.*OPENING_CASH"):
        deserialize_ledger_payload(
            LedgerEntryType.OPENING_CASH,
            '{"amount":"1","currency":"CNY","unknown":"field"}',
            entry_id="entry-2",
        )
