"""Core producer of the exact V1 RQAlpha cold-start wire contract."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class BootstrapPosition:
    code: str
    market: str
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class RQAlphaLiveBootstrapSnapshot:
    deployment_id: str
    portfolio_id: str
    broker_account_id: str
    ledger_sequence: int
    generated_at: datetime
    base_currency: str
    cash: Decimal
    positions: tuple[BootstrapPosition, ...]

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "deployment_id": self.deployment_id,
            "portfolio_id": self.portfolio_id,
            "broker_account_id": self.broker_account_id,
            "ledger_sequence": self.ledger_sequence,
            "generated_at": self.generated_at.isoformat(),
            "base_currency": self.base_currency,
            "cash": format(self.cash, "f"),
            "positions": [
                {"code": position.code, "market": position.market, "quantity": format(position.quantity, "f")}
                for position in self.positions
            ],
        }
