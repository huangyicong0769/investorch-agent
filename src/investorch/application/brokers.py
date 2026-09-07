from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from investorch.config import AppConfig
from investorch.portfolio import storage
from investorch.portfolio.domain import Broker, BrokerAccount, JsonValue


class BrokerOperations:
    """Register and read execution identity metadata without a broker connection."""

    def __init__(self, *, config: AppConfig) -> None:
        self._config = config

    async def create_broker(
        self,
        *,
        provider: str,
        display_name: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> Broker:
        now = datetime.now(UTC)
        broker = Broker(uuid.uuid4().hex, provider, display_name, now, now, {} if metadata is None else metadata)
        await asyncio.to_thread(storage.create_broker, self._config.portfolio_db, broker)
        return broker

    async def get_broker(self, broker_id: str) -> Broker | None:
        return await asyncio.to_thread(storage.get_broker, self._config.portfolio_db, broker_id)

    async def list_brokers(self) -> list[Broker]:
        return await asyncio.to_thread(storage.list_brokers, self._config.portfolio_db)

    async def create_broker_account(
        self,
        broker_id: str,
        *,
        external_account_id: str,
        display_name: str,
        account_type: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> BrokerAccount:
        now = datetime.now(UTC)
        account = BrokerAccount(
            uuid.uuid4().hex,
            broker_id,
            external_account_id,
            display_name,
            account_type,
            now,
            now,
            {} if metadata is None else metadata,
        )
        await asyncio.to_thread(storage.create_broker_account, self._config.portfolio_db, account)
        return account

    async def get_broker_account(self, broker_account_id: str) -> BrokerAccount | None:
        return await asyncio.to_thread(storage.get_broker_account, self._config.portfolio_db, broker_account_id)

    async def list_broker_accounts(self, *, broker_id: str | None = None) -> list[BrokerAccount]:
        return await asyncio.to_thread(storage.list_broker_accounts, self._config.portfolio_db, broker_id=broker_id)
