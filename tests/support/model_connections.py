"""Use controlled SDK Models for Host behavior tests without provider traffic."""

from collections.abc import Callable

from agents import Model, ModelSettings
from pytest import MonkeyPatch

from investorch.config import AgentModelConfig, AppConfig


def install_host_models(
    monkeypatch: MonkeyPatch, for_role: Callable[[AppConfig, str], tuple[Model, ModelSettings]]
) -> None:
    class ControlledConnections:
        def __init__(self, config: AppConfig, _auth: object) -> None:
            self.config = config

        def for_agent(self, role: str) -> tuple[Model, ModelSettings]:
            return for_role(self.config, role)

        def resolve(self, _selected: AgentModelConfig) -> Model:
            return for_role(self.config, "main")[0]

        async def close(self) -> None:
            pass

    monkeypatch.setattr("investorch.application.host.ModelConnectionClients", ControlledConnections)
