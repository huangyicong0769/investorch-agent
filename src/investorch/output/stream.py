import asyncio
import base64
import binascii
import json
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from agents import ToolOutputImage, ToolOutputText
from openai.types.responses import (
    Response,
    ResponseCreatedEvent,
    ResponseFunctionWebSearch,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputTextAnnotationAddedEvent,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseTextDeltaEvent,
)

from investorch.images import ImageContent, detect_image_media_type

from .events import (
    AgentChanged,
    AssistantMessage,
    AssistantMessageUpdate,
    OutputHandler,
    Reasoning,
    ToolCalled,
    ToolOutput,
    UrlCitation,
)


@dataclass
class _PendingMessage:
    provider_id: str
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parts: dict[int, list[str]] = field(default_factory=dict)
    annotations: dict[int, dict[tuple[int, int, str], UrlCitation]] = field(default_factory=dict)
    next_update_at: float = 0
    dirty: bool = False

    @property
    def text(self) -> str:
        return "".join(part for index in sorted(self.parts) for part in self.parts[index])

    def add_annotation(self, index: int, annotation: object) -> None:
        if (citation := UrlCitation.from_annotation(annotation)) is not None:
            key = (citation.start_index, citation.end_index, citation.url)
            self.annotations.setdefault(index, {})[key] = citation
            self.dirty = True

    @property
    def citations(self) -> tuple[UrlCitation, ...]:
        citations = []
        offset = 0
        for index, chunks in sorted(self.parts.items()):
            count = sum(map(len, chunks))
            citations.extend(
                replace(citation, start_index=offset + citation.start_index, end_index=offset + citation.end_index)
                for citation in self.annotations.get(index, {}).values()
                if citation.end_index <= count
            )
            offset += count
        return tuple(citations)

    def finish(self, item: ResponseOutputMessage, *, deepseek: bool) -> AssistantMessage:
        self.parts = {index: [part.text] for index, part in enumerate(item.content) if part.type == "output_text"}
        if deepseek:
            for index, part in enumerate(item.content):
                if part.type == "output_text":
                    for annotation in part.annotations or ():
                        self.add_annotation(index, annotation)
        return self.message()

    def message(self) -> AssistantMessage:
        return AssistantMessage(text=self.text, message_id=self.message_id, citations=self.citations)


def _message_text(message: ResponseOutputMessage) -> str:
    return "".join(part.text for part in message.content if part.type == "output_text")


@dataclass
class _PendingReasoning:
    text: list[str] = field(default_factory=list)
    summary: dict[int, list[str]] = field(default_factory=dict)

    @property
    def summary_text(self) -> str:
        summary = "\n\n".join("".join(self.summary[index]) for index in sorted(self.summary))
        return summary if summary.strip() else ""

    @property
    def display_text(self) -> str:
        return "".join(self.text) or self.summary_text


async def consume_run_events(
    result,
    output_handler: OutputHandler,
    current_agent_name: str,
    *,
    deepseek: bool = False,
    response_handler: Callable[[Response], None] | None = None,
) -> str:
    pending_reasoning: dict[str, _PendingReasoning] = {}
    emitted_reasoning: set[str] = set()
    pending_messages: dict[int, _PendingMessage] = {}
    completed_messages: Counter[tuple[str, str]] = Counter()
    pending_web_calls: dict[str, ResponseFunctionWebSearch] = {}
    completed_web_calls: set[str] = set()
    loop = asyncio.get_running_loop()

    async def flush_reasoning() -> None:
        for item_id, pending in pending_reasoning.items():
            if pending.display_text:
                await output_handler(Reasoning(text=pending.display_text, summary=pending.summary_text or None))
                emitted_reasoning.add(item_id)
        pending_reasoning.clear()

    def complete_reasoning(item: ResponseReasoningItem) -> None:
        if item.id in emitted_reasoning:
            return
        pending = pending_reasoning.setdefault(item.id, _PendingReasoning())
        # Completed items also cover providers that send no summary deltas.
        if any(part.text.strip() for part in item.summary):
            pending.summary = {index: [part.text] for index, part in enumerate(item.summary)}
        if item.content:
            pending.text = [part.text for part in item.content]

    async def flush_updates() -> None:
        for pending in pending_messages.values():
            if pending.dirty and loop.time() >= pending.next_update_at:
                await output_handler(AssistantMessageUpdate(pending.message_id, pending.text, pending.citations))
                pending.dirty = False
                pending.next_update_at = loop.time() + 0.05

    async def flush_messages() -> None:
        # Preserve words already shown if a provider fails or the run is stopped.
        # The run-ended event, rather than this text record, carries the outcome.
        for index in list(pending_messages):
            pending = pending_messages.pop(index)
            if pending.text:
                await output_handler(pending.message())

    async def record_web_call(item: object, *, incomplete: bool = False) -> bool:
        if not deepseek or not isinstance(item, ResponseFunctionWebSearch) or not getattr(item, "kind", None):
            return False
        if item.id in completed_web_calls:
            return True
        completed_web_calls.add(item.id)
        pending_web_calls.pop(item.id, None)
        name = {"read_link": "URL reading", "image_search": "image search", "deep_image_search": "image search"}.get(
            item.kind, "web search"
        )
        raw = item.model_dump(mode="json", exclude_unset=True)
        arguments = json.dumps({"kind": item.kind, "action": raw.get("action")}, ensure_ascii=False)
        await flush_reasoning()
        await output_handler(ToolCalled(f"DeepSeek {name}", arguments, "incomplete" if incomplete else item.status))
        return True

    stream = result.stream_events().__aiter__()
    next_event = None
    try:
        while True:
            if next_event is None:
                next_event = asyncio.create_task(anext(stream))
            deadlines = [pending.next_update_at for pending in pending_messages.values() if pending.dirty]
            timeout = max(0, min(deadlines) - loop.time()) if deadlines else None
            # Wait on the stream and the refresh deadline together: a provider
            # pause must not leave its last received words hidden indefinitely.
            ready, _ = await asyncio.wait((next_event,), timeout=timeout)
            await flush_updates()
            if not ready:
                continue
            completed_event, next_event = next_event, None
            try:
                event = completed_event.result()
            except StopAsyncIteration:
                break
            if event.type == "agent_updated_stream_event":
                await flush_reasoning()
                new_agent_name = event.new_agent.name
                if new_agent_name != current_agent_name:
                    current_agent_name = new_agent_name
                    await output_handler(AgentChanged(name=new_agent_name))
                continue

            if event.type == "raw_response_event":
                if isinstance(event.data, (ResponseReasoningTextDeltaEvent, ResponseReasoningSummaryTextDeltaEvent)):
                    delta = event.data
                    if delta.item_id not in emitted_reasoning:
                        pending = pending_reasoning.setdefault(delta.item_id, _PendingReasoning())
                        if isinstance(delta, ResponseReasoningSummaryTextDeltaEvent):
                            pending.summary.setdefault(delta.summary_index, []).append(delta.delta)
                        else:
                            pending.text.append(delta.delta)
                elif isinstance(event.data, ResponseCreatedEvent):
                    # Chat Completions adapters can reuse item IDs on every model turn.
                    await flush_reasoning()
                    emitted_reasoning.clear()
                    await flush_messages()
                    completed_messages.clear()
                    completed_web_calls.clear()
                elif (
                    deepseek
                    and isinstance(event.data, ResponseOutputItemAddedEvent)
                    and isinstance(event.data.item, ResponseFunctionWebSearch)
                ):
                    pending_web_calls[event.data.item.id] = event.data.item
                elif isinstance(event.data, ResponseOutputItemDoneEvent) and await record_web_call(event.data.item):
                    continue
                elif deepseek and event.data.type in {"response.completed", "response.failed", "response.incomplete"}:
                    if response_handler is not None:
                        response_handler(event.data.response)
                    for item in event.data.response.output or ():
                        await record_web_call(item)
                elif isinstance(event.data, ResponseTextDeltaEvent):
                    await flush_reasoning()
                    delta = event.data
                    if delta.output_index not in pending_messages:
                        pending_messages[delta.output_index] = _PendingMessage(delta.item_id)
                    pending = pending_messages[delta.output_index]
                    pending.parts.setdefault(delta.content_index, []).append(delta.delta)
                    pending.dirty = True
                    await flush_updates()
                elif deepseek and isinstance(event.data, ResponseOutputTextAnnotationAddedEvent):
                    annotation = event.data
                    if annotation.output_index not in pending_messages:
                        pending_messages[annotation.output_index] = _PendingMessage(annotation.item_id)
                    pending_messages[annotation.output_index].add_annotation(
                        annotation.content_index, annotation.annotation
                    )
                elif isinstance(event.data, ResponseOutputItemDoneEvent) and isinstance(
                    event.data.item, ResponseReasoningItem
                ):
                    complete_reasoning(event.data.item)
                    await flush_reasoning()
                elif isinstance(event.data, ResponseOutputItemDoneEvent) and isinstance(
                    event.data.item, ResponseOutputMessage
                ):
                    await flush_reasoning()
                    item = event.data.item
                    text = _message_text(item)
                    pending = pending_messages.pop(event.data.output_index, None)
                    completed_messages[item.id, text] += 1
                    await output_handler((pending or _PendingMessage(item.id)).finish(item, deepseek=deepseek))
                continue

            if event.type != "run_item_stream_event":
                continue

            if event.name == "reasoning_item_created":
                complete_reasoning(event.item.raw_item)
                await flush_reasoning()
                continue

            # Be defensive in case a provider does not emit
            # reasoning_item_created exactly as expected.
            await flush_reasoning()

            item = event.item

            if event.name == "tool_called":
                raw_item = item.raw_item
                if await record_web_call(raw_item):
                    continue
                arguments = (
                    raw_item.get("arguments") if isinstance(raw_item, dict) else getattr(raw_item, "arguments", None)
                )
                await output_handler(ToolCalled(name=item.tool_name, arguments=arguments))
            elif event.name == "tool_output":
                await output_handler(tool_output_from_sdk(item.output))
            elif event.name == "message_output_created":
                text = _message_text(item.raw_item)
                key = (item.raw_item.id, text)
                if completed_messages[key]:
                    completed_messages[key] -= 1
                    continue
                pending = next(
                    (message for message in pending_messages.values() if message.provider_id == item.raw_item.id), None
                )
                if pending is not None:
                    pending_messages = {
                        index: message for index, message in pending_messages.items() if message is not pending
                    }
                await output_handler(
                    (pending or _PendingMessage(item.raw_item.id)).finish(item.raw_item, deepseek=deepseek)
                )
    finally:
        if next_event is not None:
            next_event.cancel()
            await asyncio.gather(next_event, return_exceptions=True)
        for call in list(pending_web_calls.values()):
            await record_web_call(call, incomplete=True)
        await flush_messages()

    await flush_reasoning()
    return current_agent_name


def tool_output_from_sdk(output: object) -> ToolOutput:
    """Preserve the SDK's structured text/image observations without dumping images."""
    parts = output if isinstance(output, (list, tuple)) else [output]
    texts: list[str] = []
    images: list[ImageContent] = []
    for part in parts:
        if isinstance(part, ToolOutputText):
            texts.append(part.text)
        elif isinstance(part, ToolOutputImage):
            if part.image_url:
                images.append(ImageContent(part.image_url, detail=part.detail or "auto"))
            else:
                texts.append("[image: provider file]")
        elif isinstance(part, dict) and part.get("type") in ("image", "input_image"):
            url = part.get("image_url")
            if isinstance(url, str):
                images.append(ImageContent(url, detail=part.get("detail") or "auto"))
            else:
                texts.append("[image: provider file]")
        elif isinstance(part, dict) and part.get("type") in ("text", "input_text"):
            texts.append(str(part.get("text", "")))
        else:
            texts.append(str(part))
    return ToolOutput(output="\n".join(texts), images=tuple(images))


def assistant_message_from_result(result) -> AssistantMessage:
    """Keep final text semantics and image output exposed by standard SDK run items."""
    if isinstance(result.final_output, AssistantMessage):
        return result.final_output
    images: list[ImageContent] = []
    for item in result.new_items:
        raw = item.raw_item
        if getattr(raw, "type", None) == "image_generation_call" and getattr(raw, "result", None):
            try:
                media_type = detect_image_media_type(base64.b64decode(raw.result, validate=True))
            except (ValueError, binascii.Error):
                continue
            if media_type:
                images.append(ImageContent(f"data:{media_type};base64," + raw.result, media_type=media_type))
    return AssistantMessage(text=str(result.final_output), images=tuple(images))
