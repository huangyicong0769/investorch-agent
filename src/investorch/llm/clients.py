"""Host-owned SDK clients, shared by connection and closed with the Host."""

import asyncio
from collections.abc import Callable
from functools import partial

from agents import Model, ModelSettings, OpenAIResponsesModel
from openai import AsyncOpenAI

from investorch.config import AgentModelConfig, AppConfig
from investorch.llm.oauth import ChatGPTAuth


class ModelConnectionClients:
    def __init__(
        self,
        config: AppConfig,
        chatgpt_auth: ChatGPTAuth,
        *,
        client_factory: Callable[..., AsyncOpenAI] = AsyncOpenAI,
    ) -> None:
        self._config = config
        self._auth = chatgpt_auth
        self._client_factory = client_factory
        self._clients: dict[str, AsyncOpenAI] = {}
        self._closed = False

    def resolve(self, selected: AgentModelConfig) -> Model:
        if self._closed:
            raise RuntimeError("Model connections are closed")
        connection = self._config.model_connection(selected.model)
        client = self._clients.get(selected.model)
        if client is None:
            if connection.type == "chatgpt":
                client = self._client_factory(
                    api_key=partial(self._auth.access_token, connection.credentials),
                    base_url="https://api.openai.com/v1",
                )
            else:
                client = self._client_factory(
                    api_key=self._config.secret(connection.api_key_secret), base_url=connection.base_url
                )
            self._clients[selected.model] = client
        if connection.type == "chatgpt":
            from investorch.llm.models import ChatGPTResponsesModel

            return ChatGPTResponsesModel(selected.name, client)
        return OpenAIResponsesModel(model=selected.name, openai_client=client)

    def for_agent(self, role: str) -> tuple[Model, ModelSettings]:
        selected = self._config.agent_model(role)
        return self.resolve(selected), ModelSettings(reasoning={"effort": selected.reasoning_effort})

    async def close(self) -> None:
        self._closed = True
        try:
            results = await asyncio.gather(
                *(client.close() for client in self._clients.values()), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            self._clients.clear()
