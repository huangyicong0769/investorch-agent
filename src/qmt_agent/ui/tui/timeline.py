import json

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible, Label, Markdown, Static

from qmt_agent.output import AgentChanged, AssistantMessage, OutputEvent, Reasoning, ToolCalled, ToolOutput


def format_json(text: str | None) -> str:
    if not text:
        return ""

    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return text


class UserMessageWidget(Vertical):
    def __init__(self, text: str) -> None:
        super().__init__(
            Label("You", classes="message-author"),
            Static(text, markup=False, classes="message-content"),
            classes="user-message",
        )


class AssistantMessageWidget(Vertical):
    def __init__(self, text: str) -> None:
        super().__init__(
            Label("QMT Agent", classes="message-author"),
            Markdown(text, classes="assistant-markdown"),
            classes="assistant-message",
        )


class SystemNotice(Static):
    def __init__(self, text: str) -> None:
        super().__init__(text, markup=False, classes="system-notice")


class ActivitySection(Vertical):
    def __init__(self, heading: str) -> None:
        self._body = Static(markup=False, classes="activity-section-body")
        super().__init__(
            Label(heading, classes="activity-section-heading"),
            self._body,
            classes="activity-section",
        )
        self.display = False

    def set_content(self, text: str) -> None:
        self._body.update(text)
        self.display = True


class ActivityStep(Collapsible):
    def __init__(self, title: str = "正在思考…") -> None:
        self.reasoning_parts: list[str] = []
        self.tool_name: str | None = None
        self.tool_arguments: str | None = None
        self.target_seq: int | None = None
        self.session_id: str | None = None
        self._reasoning = ActivitySection("Reasoning")
        self._tool = ActivitySection("Tool")
        self._approval = ActivitySection("Approval")
        self._observation = ActivitySection("Observation")
        super().__init__(
            self._reasoning,
            self._tool,
            self._approval,
            self._observation,
            title=title,
            collapsed=True,
            classes="activity-step",
        )

    def append_reasoning(self, text: str) -> None:
        self.reasoning_parts.append(text)
        self._reasoning.set_content("".join(self.reasoning_parts))

    def set_tool(self, name: str, arguments: str | None) -> None:
        self.tool_name = name
        self.tool_arguments = arguments
        body = f"Tool · {name}"
        formatted = format_json(arguments)
        if formatted:
            body = f"{body}\n\n{formatted}"
        self._tool.set_content(body)
        self.title = f"正在调用 {name}"

    def set_observation(self, output: str) -> None:
        self._observation.set_content(output)

    def set_approval(self, approved: bool) -> None:
        self._approval.set_content("✓ Approved" if approved else "✗ Rejected")

    def set_activity_label(self, label: str) -> None:
        self.title = label


class ChatTimeline(VerticalScroll):
    def __init__(self, *children, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self._current_step: ActivityStep | None = None

    @property
    def current_step(self) -> ActivityStep | None:
        return self._current_step

    async def add_user_message(self, text: str) -> None:
        self._current_step = None
        await self.mount(UserMessageWidget(text))
        self.scroll_end(animate=False)

    async def add_assistant_message(self, text: str) -> None:
        self._current_step = None
        await self.mount(AssistantMessageWidget(text))
        self.scroll_end(animate=False)

    async def add_notice(self, text: str) -> None:
        await self.mount(SystemNotice(text))
        self.scroll_end(animate=False)

    async def add_agent_changed(self, name: str) -> None:
        if self._current_step and self._current_step.tool_name is None:
            self._current_step = None
        await self.mount(SystemNotice(f"Agent → {name}"))
        self.scroll_end(animate=False)

    async def add_reasoning(self, text: str) -> ActivityStep:
        if self._current_step is None:
            self._current_step = ActivityStep()
            await self.mount(self._current_step)
        self._current_step.append_reasoning(text)
        self.scroll_end(animate=False)
        return self._current_step

    async def add_tool_call(self, name: str, arguments: str | None) -> ActivityStep:
        if self._current_step is None or self._current_step.tool_name is not None:
            self._current_step = ActivityStep()
            await self.mount(self._current_step)
        self._current_step.set_tool(name, arguments)
        self.scroll_end(animate=False)
        return self._current_step

    async def add_tool_output(self, output: str) -> ActivityStep:
        if self._current_step is None:
            self._current_step = ActivityStep("Tool output")
            await self.mount(self._current_step)
        step = self._current_step
        step.set_observation(output)
        self._current_step = None
        self.scroll_end(animate=False)
        return step

    async def handle_output(self, event: OutputEvent) -> ActivityStep | None:
        if isinstance(event, AgentChanged):
            await self.add_agent_changed(event.name)
        elif isinstance(event, Reasoning):
            return await self.add_reasoning(event.text)
        elif isinstance(event, ToolCalled):
            return await self.add_tool_call(event.name, event.arguments)
        elif isinstance(event, ToolOutput):
            return await self.add_tool_output(event.output)
        elif isinstance(event, AssistantMessage):
            await self.add_assistant_message(event.text)
        return None

    async def reset(self) -> None:
        self._current_step = None
        await self.remove_children()
