from .events import AgentChanged, AssistantMessage, OutputEvent, Reasoning, ToolCalled, ToolOutput


def serialize_output_event(event: OutputEvent) -> dict[str, object]:
    if isinstance(event, AgentChanged):
        return {"type": "agent_changed", "name": event.name}
    if isinstance(event, Reasoning):
        return {"type": "reasoning", "text": event.text}
    if isinstance(event, ToolCalled):
        return {"type": "tool_called", "name": event.name, "arguments": event.arguments}
    if isinstance(event, ToolOutput):
        return {"type": "tool_output", "output": event.output}
    if isinstance(event, AssistantMessage):
        return {"type": "assistant_message", "text": event.text}

    raise TypeError(f"Unsupported output event: {type(event).__name__}")
