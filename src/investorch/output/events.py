from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from investorch.images import ImageContent


@dataclass(frozen=True, slots=True)
class AgentChanged:
    name: str


@dataclass(frozen=True, slots=True)
class Reasoning:
    text: str
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCalled:
    name: str
    arguments: str | None = None


@dataclass(frozen=True, slots=True)
class ToolOutput:
    output: str
    images: tuple[ImageContent, ...] = ()


@dataclass(frozen=True, slots=True)
class UrlCitation:
    start_index: int
    end_index: int
    url: str
    title: str

    @classmethod
    def from_annotation(cls, annotation: object) -> "UrlCitation | None":
        if getattr(annotation, "type", None) != "url_citation":
            return None
        start, end = getattr(annotation, "start_index", None), getattr(annotation, "end_index", None)
        url = getattr(annotation, "url", None)
        if type(start) is not int or type(end) is not int or not 0 <= start <= end or not isinstance(url, str):
            return None
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return cls(start, end, url, getattr(annotation, "title", "") or "")


def render_citations(text: str, citations: tuple[UrlCitation, ...]) -> str:
    """Insert Markdown links using the API's Unicode code point offsets."""
    parts: list[str] = []
    source_numbers: dict[str, int] = {}
    start = 0
    for citation in sorted(citations, key=lambda item: item.end_index):
        url = quote(citation.url, safe=":/?#[]@!$&'*+,;=%")
        number = source_numbers.setdefault(citation.url, len(source_numbers) + 1)
        parts.extend([text[start : citation.end_index], f" [{number}](<{url}>)"])
        start = citation.end_index
    return "".join([*parts, text[start:]])


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    text: str
    images: tuple[ImageContent, ...] = ()
    message_id: str | None = None
    citations: tuple[UrlCitation, ...] = ()


@dataclass(frozen=True, slots=True)
class AssistantMessageUpdate:
    message_id: str
    text: str
    citations: tuple[UrlCitation, ...] = ()


OutputEvent = AgentChanged | Reasoning | ToolCalled | ToolOutput | AssistantMessage | AssistantMessageUpdate
OutputHandler = Callable[[OutputEvent], Awaitable[None]]
