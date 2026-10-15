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
    message_id: str | None = None


@dataclass(frozen=True, slots=True)
class AssistantMessageUpdate:
    message_id: str
    text: str


OutputEvent = AgentChanged | Reasoning | ToolCalled | ToolOutput | AssistantMessage | AssistantMessageUpdate
OutputHandler = Callable[[OutputEvent], Awaitable[None]]
