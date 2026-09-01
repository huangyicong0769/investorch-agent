from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentChanged:
    name: str


@dataclass(frozen=True, slots=True)
class Reasoning:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCalled:
    name: str
    arguments: str | None = None


@dataclass(frozen=True, slots=True)
class ToolOutput:
    output: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    text: str


OutputEvent = AgentChanged | Reasoning | ToolCalled | ToolOutput | AssistantMessage
OutputHandler = Callable[[OutputEvent], Awaitable[None]]
