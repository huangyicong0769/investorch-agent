from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from investorch.images import ImageContent


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
    images: tuple[ImageContent, ...] = ()


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    text: str
    images: tuple[ImageContent, ...] = ()


OutputEvent = AgentChanged | Reasoning | ToolCalled | ToolOutput | AssistantMessage
OutputHandler = Callable[[OutputEvent], Awaitable[None]]
