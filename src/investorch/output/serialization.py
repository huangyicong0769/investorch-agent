from investorch.images import serialize_images

from .events import AgentChanged, AssistantMessage, OutputEvent, Reasoning, ToolCalled, ToolOutput


def serialize_output_event(event: OutputEvent) -> dict[str, object]:
    if isinstance(event, AgentChanged):
        return {"type": "agent_changed", "name": event.name}
    if isinstance(event, Reasoning):
        return {"type": "reasoning", "text": event.text}
    if isinstance(event, ToolCalled):
        return {"type": "tool_called", "name": event.name, "arguments": event.arguments}
    if isinstance(event, ToolOutput):
        return {
            "type": "tool_output",
            "output": event.output,
            **({"images": serialize_images(event.images)} if event.images else {}),
        }
    if isinstance(event, AssistantMessage):
        return {
            "type": "assistant_message",
            "text": event.text,
            **({"images": serialize_images(event.images)} if event.images else {}),
        }

    raise TypeError(f"Unsupported output event: {type(event).__name__}")
