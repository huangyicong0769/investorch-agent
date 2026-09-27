import base64
import binascii

from agents import ToolOutputImage, ToolOutputText
from openai.types.responses import ResponseReasoningTextDeltaEvent

from investorch.images import ImageContent, detect_image_media_type

from .events import AgentChanged, AssistantMessage, OutputHandler, Reasoning, ToolCalled, ToolOutput


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

    async for event in result.stream_events():
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
            pass

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
