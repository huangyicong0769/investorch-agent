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
        follow_output = self.is_vertical_scroll_end
        self._current_step = None
        await self.mount(AssistantMessageWidget(text))
        self._follow_output(follow_output)

    async def add_notice(self, text: str) -> None:
        follow_output = self.is_vertical_scroll_end
        await self.mount(SystemNotice(text))
        self._follow_output(follow_output)

    async def add_agent_changed(self, name: str) -> None:
        follow_output = self.is_vertical_scroll_end
        if self._current_step and self._current_step.tool_name is None:
            self._current_step = None
        await self.mount(SystemNotice(f"Agent → {name}"))
        self._follow_output(follow_output)

    async def add_reasoning(self, text: str) -> ActivityStep:
        follow_output = self.is_vertical_scroll_end
        if self._current_step is None:
            self._current_step = ActivityStep()
            await self.mount(self._current_step)
        self._current_step.append_reasoning(text)
        self._follow_output(follow_output)
        return self._current_step

    async def add_tool_call(self, name: str, arguments: str | None) -> ActivityStep:
        follow_output = self.is_vertical_scroll_end
        if self._current_step is None or self._current_step.tool_name is not None:
            self._current_step = ActivityStep()
            await self.mount(self._current_step)
        self._current_step.set_tool(name, arguments)
        self._follow_output(follow_output)
        return self._current_step

    async def add_approval(self, approved: bool) -> None:
        if self._current_step is None:
            await self.add_notice("Tool action approved." if approved else "Tool action rejected.")
            return

        self._current_step.set_approval(approved)
        if not approved:
            self._current_step = None

    async def add_tool_output(self, output: str) -> ActivityStep:
        follow_output = self.is_vertical_scroll_end
        if self._current_step is None:
            self._current_step = ActivityStep("Tool output")
            await self.mount(self._current_step)
        step = self._current_step
        step.set_observation(output)
        self._current_step = None
        self._follow_output(follow_output)
        return step

    def _follow_output(self, follow: bool) -> None:
        if follow:
            self.scroll_end(animate=False)

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

    async def render_history(self, records: list[dict[str, object]]) -> None:
        await self.reset()
        activity_labels: dict[int, str] = {}

        for record in records:
            if record.get("type") != "activity_label":
                continue
            target_seq = record.get("target_seq")
            text = record.get("text")
            if type(target_seq) is int and target_seq >= 1 and isinstance(text, str) and text.strip():
                activity_labels[target_seq] = text.strip()

        for record in records:
            event_type = record["type"]

            if event_type == "activity_label":
                continue
            if event_type == "user_message" and isinstance(record.get("text"), str):
                await self.add_user_message(record["text"])
            elif event_type == "reasoning" and isinstance(record.get("text"), str):
                await self.add_reasoning(record["text"])
            elif event_type == "tool_called" and isinstance(record.get("name"), str):
                arguments = record.get("arguments")
                step = await self.add_tool_call(
                    record["name"],
                    arguments if isinstance(arguments, str) else None,
                )
                seq = record["seq"]
                if type(seq) is int:
                    step.target_seq = seq
                    label = activity_labels.get(seq)
                    if label:
                        step.set_activity_label(label)
            elif event_type == "tool_output" and isinstance(record.get("output"), str):
                await self.add_tool_output(record["output"])
            elif event_type == "assistant_message" and isinstance(record.get("text"), str):
                await self.add_assistant_message(record["text"])
            elif event_type == "agent_changed" and isinstance(record.get("name"), str):
                await self.add_agent_changed(record["name"])
            elif event_type == "approval" and type(record.get("approved")) is bool:
                await self.add_approval(record["approved"])
