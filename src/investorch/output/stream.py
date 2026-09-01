from openai.types.responses import ResponseReasoningTextDeltaEvent

from .events import AgentChanged, OutputHandler, Reasoning, ToolCalled, ToolOutput


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
            await output_handler(ToolOutput(output=str(item.output)))
        elif event.name == "message_output_created":
            pass

    await _flush_reasoning(reasoning_parts, output_handler)
    return current_agent_name
