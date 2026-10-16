import asyncio
import base64
import binascii
import uuid
from collections import Counter
from dataclasses import dataclass, field

from agents import ToolOutputImage, ToolOutputText
from openai.types.responses import (
    ResponseCreatedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
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
)


@dataclass
class _PendingMessage:
    provider_id: str
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parts: dict[int, list[str]] = field(default_factory=dict)
    next_update_at: float = 0
    dirty: bool = False

    @property
    def text(self) -> str:
        return "".join(part for index in sorted(self.parts) for part in self.parts[index])


def _message_text(message: ResponseOutputMessage) -> str:
    return "".join(part.text for part in message.content if part.type == "output_text")


async def _flush_reasoning(
    reasoning_parts: list[str],
    output_handler: OutputHandler,
) -> None:
    if not reasoning_parts:
        return

    text = "".join(reasoning_parts)
    reasoning_parts.clear()
    await output_handler(Reasoning(text=text))


async def consume_run_events(
    result,
    output_handler: OutputHandler,
    current_agent_name: str,
) -> str:
    reasoning_parts: list[str] = []
    pending_messages: dict[int, _PendingMessage] = {}
    completed_messages: Counter[tuple[str, str]] = Counter()
    loop = asyncio.get_running_loop()

    async def flush_updates() -> None:
        for pending in pending_messages.values():
            if pending.dirty and loop.time() >= pending.next_update_at:
                await output_handler(AssistantMessageUpdate(message_id=pending.message_id, text=pending.text))
                pending.dirty = False
                pending.next_update_at = loop.time() + 0.05

    async def flush_messages() -> None:
        # Preserve words already shown if a provider fails or the run is stopped.
        # The run-ended event, rather than this text record, carries the outcome.
        for index in list(pending_messages):
            pending = pending_messages.pop(index)
            if pending.text:
                await output_handler(AssistantMessage(text=pending.text, message_id=pending.message_id))

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
                await _flush_reasoning(reasoning_parts, output_handler)
                new_agent_name = event.new_agent.name
                if new_agent_name != current_agent_name:
                    current_agent_name = new_agent_name
                    await output_handler(AgentChanged(name=new_agent_name))
                continue

            if event.type == "raw_response_event":
                if isinstance(event.data, ResponseReasoningTextDeltaEvent):
                    reasoning_parts.append(event.data.delta)
                elif isinstance(event.data, ResponseCreatedEvent):
                    # Chat Completions adapters can reuse item IDs on every model turn.
                    await flush_messages()
                    completed_messages.clear()
                elif isinstance(event.data, ResponseTextDeltaEvent):
                    await _flush_reasoning(reasoning_parts, output_handler)
                    delta = event.data
                    if delta.output_index not in pending_messages:
                        pending_messages[delta.output_index] = _PendingMessage(delta.item_id)
                    pending = pending_messages[delta.output_index]
                    pending.parts.setdefault(delta.content_index, []).append(delta.delta)
                    pending.dirty = True
                    await flush_updates()
                elif isinstance(event.data, ResponseOutputItemDoneEvent) and isinstance(
                    event.data.item, ResponseOutputMessage
                ):
                    await _flush_reasoning(reasoning_parts, output_handler)
                    item = event.data.item
                    text = _message_text(item)
                    pending = pending_messages.pop(event.data.output_index, None)
                    completed_messages[item.id, text] += 1
                    await output_handler(
                        AssistantMessage(text=text, message_id=pending.message_id if pending else uuid.uuid4().hex)
                    )
                continue

            if event.type != "run_item_stream_event":
                continue

            if event.name == "reasoning_item_created":
                await _flush_reasoning(reasoning_parts, output_handler)
                continue

            # Be defensive in case a provider does not emit
            # reasoning_item_created exactly as expected.
            await _flush_reasoning(reasoning_parts, output_handler)

            item = event.item

            if event.name == "tool_called":
                raw_item = item.raw_item
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
                    AssistantMessage(
                        text=text,
                        message_id=pending.message_id if pending else uuid.uuid4().hex,
                    )
                )
    finally:
        if next_event is not None:
            next_event.cancel()
            await asyncio.gather(next_event, return_exceptions=True)
        await flush_messages()

    await _flush_reasoning(reasoning_parts, output_handler)
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
