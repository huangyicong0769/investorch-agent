import asyncio
import json
from qmt_agent.output import (
    AgentChanged,
    AssistantMessage,
    OutputEvent,
    Reasoning,
    ToolCalled,
    ToolOutput,
)

class ConsoleUI:
    def write(self, text: str) -> None:
        print(text)

    async def read_user_input(self) -> str:
        return await asyncio.to_thread(input, "You: ")

    async def request_tool_approval(
        self,
        tool_name: str,
        arguments: str | None,
    ) -> bool:
        return await asyncio.to_thread(
            self._request_tool_approval_sync,
            tool_name,
            arguments,
        )

    def _request_tool_approval_sync(
        self,
        tool_name: str,
        arguments: str | None,
    ) -> bool:
        self.write(f"\n[approval] {tool_name}")

        if arguments:
            self.write(arguments)

        answer = input("Approve? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}


class ConsoleRenderer:
    def __init__(self, ui: ConsoleUI) -> None:
        self._ui = ui

    async def handle(self, event: OutputEvent) -> None:
        if isinstance(event, AgentChanged):
            self._ui.write(f"\n[agent] {event.name}")
        elif isinstance(event, Reasoning):
            self._render_trace_content("reasoning", event.text)
        elif isinstance(event, ToolCalled):
            self._render_tool_call(event)
        elif isinstance(event, ToolOutput):
            self._render_trace_content("observation", event.output)
        elif isinstance(event, AssistantMessage):
            self._ui.write(f"Agent: {event.text}")

    def _render_trace_content(self, kind: str, text: str) -> None:
        self._ui.write(f"\n[{kind}]")
        self._ui.write(text)

    def _render_tool_call(self, event: ToolCalled) -> None:
        self._ui.write(f"\n[action] {event.name}")

        if not event.arguments:
            return

        try:
            parsed = json.loads(event.arguments)
            arguments = json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            arguments = event.arguments

        self._ui.write(arguments)
