import asyncio
import json

from agents import Agent, Runner

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
    def __init__(
        self,
        ui: ConsoleUI,
        summary_agent: Agent,
        summary_enabled: bool,
        summary_threshold: int,
    ) -> None:
        self._ui = ui
        self._summary_agent = summary_agent
        self._summary_enabled = summary_enabled
        self._summary_threshold = summary_threshold

    async def handle(self, event: OutputEvent) -> None:
        if isinstance(event, AgentChanged):
            self._ui.write(f"\n[agent] {event.name}")
        elif isinstance(event, Reasoning):
            await self._render_trace_content("reasoning", event.text)
        elif isinstance(event, ToolCalled):
            self._render_tool_call(event)
        elif isinstance(event, ToolOutput):
            await self._render_trace_content("observation", event.output)
        elif isinstance(event, AssistantMessage):
            self._ui.write(f"Agent: {event.text}")

    async def _summarize_trace(self, kind: str, text: str) -> str:
        result = await Runner.run(
            self._summary_agent,
            f"Content type: {kind}.\nSummarize the following execution content:\n{text}",
        )
        summary = str(result.final_output).strip()

        if not summary:
            raise ValueError("Summary agent returned an empty summary.")

        return summary

    async def _render_trace_content(self, kind: str, text: str) -> None:
        if not self._summary_enabled or len(text) <= self._summary_threshold:
            self._ui.write(f"\n[{kind}]")
            self._ui.write(text)
            return

        try:
            summary = await self._summarize_trace(kind, text)
        except Exception as e:
            # Presentation failure should never break the main agent run.
            self._ui.write(f"\n[{kind}]")
            self._ui.write(text)
            self._ui.write(f"[summary failed: {e}]")
            return

        self._ui.write(f"\n[{kind} summary]")
        self._ui.write(summary)
        self._ui.write(f"[original: {len(text)} chars]")

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
