"""Model resolution shares connections while preserving per-request selections."""

from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from investorch.llm.clients import ModelConnectionClients
from investorch.llm.oauth import ChatGPTAuth
from tests.support.config import make_test_config


async def test_clients_are_reused_per_connection_and_closed_together(tmp_path: Path) -> None:
    config = make_test_config(
        tmp_path,
        {
            "models": {
                "second": {
                    "base_url": "https://second.example.test/v1",
                    "api_key_secret": "TEST_API_KEY",
                }
            }
        },
    )
    created: list[AsyncOpenAI] = []

    def factory(**kwargs) -> AsyncOpenAI:
        client = AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(500),
                )
            ),
        )
        created.append(client)
        return client

    auth = ChatGPTAuth(config)
    clients = ModelConnectionClients(config, auth, client_factory=factory)
    try:
        main = config.agent_model("main")
        large = clients.resolve(main)
        small = clients.resolve(replace(main, name="smaller"))
        clients.for_agent("title")
        assert len(created) == 1
        assert large.model == "test-model" and small.model == "smaller"
        clients.resolve(replace(main, model="second"))
        assert len(created) == 2
        assert all(not client.is_closed() for client in created)
        await clients.close()
        assert all(client.is_closed() for client in created)
        with pytest.raises(RuntimeError, match="closed"):
            clients.resolve(main)
    finally:
        await clients.close()
        await auth.close()
