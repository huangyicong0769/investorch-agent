from __future__ import annotations

import json
import logging
from collections import deque

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible, Label, Markdown, Static

from qmt_agent.output import AgentChanged, AssistantMessage, OutputEvent, Reasoning, ToolCalled, ToolOutput

logger = logging.getLogger(__name__)


def format_json(text: str | None) -> str:
    if not text:
        return ""

    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return text


class UserMessageWidget(Vertical):
    def __init__(self, text: str, author_margin_top: int, author_margin_bottom: int) -> None:
        author = Label("You", classes="message-author")
        author.styles.margin = (author_margin_top, 0, author_margin_bottom, 0)
        super().__init__(
            author,
            Static(text, markup=False, classes="message-content"),
            classes="user-message",
        )


class AssistantTurnWidget(Vertical):
    def __init__(self, author_margin_top: int, author_margin_bottom: int) -> None:
        author = Label("QMT Agent", classes="message-author")
        author.styles.margin = (author_margin_top, 0, author_margin_bottom, 0)
        super().__init__(
            author,
            classes="assistant-message",
        )

    async def add_activity_group(self, group: ActivityGroup) -> None:
        await self.mount(group)

    async def add_message(self, text: str) -> None:
        await self.mount(Markdown(text, classes="assistant-markdown"))


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
    def __init__(self, detail_max_height: int, title: str = "正在思考…") -> None:
        self.reasoning_parts: list[str] = []
        self.tool_name: str | None = None
        self.tool_arguments: str | None = None
        self.target_seq: int | None = None
        self.session_id: str | None = None
        self.label_reasoning = ""
        self.approval_recorded = False
        self.group: ActivityGroup | None = None
        self._reasoning = ActivitySection("Reasoning")
        self._tool = ActivitySection("Tool")
        self._approval = ActivitySection("Approval")
        self._observation = ActivitySection("Observation")
        self._details = VerticalScroll(
            self._reasoning,
            self._tool,
            self._approval,
            self._observation,
            classes="activity-details",
        )
        self._details.styles.max_height = detail_max_height
        super().__init__(
            self._details,
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

    def set_approval(
        self,
        approved: bool,
        *,
        source: str | None = None,
        review_decision: str | None = None,
        review_reason: str | None = None,
    ) -> None:
        if source == "permission":
            text = "✓ Auto-approved" if approved else "✗ Auto-rejected"
        elif source == "user":
            text = "✓ Approved by user" if approved else "✗ Rejected by user"
        else:
            text = "✓ Approved" if approved else "✗ Rejected"

        if review_reason is not None:
            label = "AutoReview asked:" if review_decision == "ask" else ""
            text = f"{text}\n\n{label}\n{review_reason}" if label else f"{text}\n\n{review_reason}"

        self._approval.set_content(text)
        self.approval_recorded = True

    def set_activity_label(self, label: str) -> None:
        self.title = label
        if self.group is not None:
            self.group.refresh_step_title(self)


class ActivityGroup(Collapsible):
    def __init__(self, panel_max_height: int) -> None:
        self._latest_step: ActivityStep | None = None
        self._steps = VerticalScroll(classes="activity-step-list")
        self._steps.styles.max_height = panel_max_height
        super().__init__(
            self._steps,
            title="正在思考…",
            collapsed=True,
            classes="activity-group",
        )

    async def add_step(self, step: ActivityStep) -> None:
        step.group = self
        self._latest_step = step
        self.title = step.title
        await self._steps.mount(step)

    def refresh_step_title(self, step: ActivityStep) -> None:
        if self._latest_step is step:
            self.title = step.title


class ChatTimeline(VerticalScroll):
    def __init__(
        self,
        activity_panel_max_height: int,
        activity_detail_max_height: int,
        message_author_margin_top: int,
        message_author_margin_bottom: int,
        initial_agent_name: str | None = None,
        *children,
        **kwargs,
    ) -> None:
        super().__init__(*children, **kwargs)
        self._activity_panel_max_height = activity_panel_max_height
        self._activity_detail_max_height = activity_detail_max_height
        self._message_author_margin_top = message_author_margin_top
        self._message_author_margin_bottom = message_author_margin_bottom
        self._initial_agent_name = initial_agent_name
        self._active_agent_name = initial_agent_name
        self._current_assistant_turn: AssistantTurnWidget | None = None
        self._current_activity_group: ActivityGroup | None = None
        self._pending_tool_outputs: deque[ActivityStep] = deque()
        self._tool_steps: list[ActivityStep] = []
        self._recent_reasoning: list[str] = []

    def _finish_activity(self) -> None:
        self._current_activity_group = None
        self._pending_tool_outputs.clear()
        self._tool_steps.clear()
        self._recent_reasoning.clear()

    def _finish_assistant_turn(self) -> None:
        self._current_activity_group = None
        self._current_assistant_turn = None

    async def _add_activity_step(self, step: ActivityStep) -> None:
        turn = await self._ensure_assistant_turn()
        if self._current_activity_group is None:
            self._current_activity_group = ActivityGroup(self._activity_panel_max_height)
            await turn.add_activity_group(self._current_activity_group)
        await self._current_activity_group.add_step(step)

    async def _ensure_assistant_turn(self) -> AssistantTurnWidget:
        if self._current_assistant_turn is None:
            self._current_assistant_turn = AssistantTurnWidget(
                self._message_author_margin_top,
                self._message_author_margin_bottom,
            )
            await self.mount(self._current_assistant_turn)
        return self._current_assistant_turn

    async def add_user_message(self, text: str) -> None:
        self._finish_activity()
        self._current_assistant_turn = None
        self._active_agent_name = self._initial_agent_name
        await self.mount(
            UserMessageWidget(
                text,
                self._message_author_margin_top,
                self._message_author_margin_bottom,
            )
        )
        self.scroll_end(animate=False)

    async def add_assistant_message(self, text: str) -> None:
        follow_output = self.is_vertical_scroll_end
        turn = await self._ensure_assistant_turn()
        self._finish_activity()
        await turn.add_message(text)
        self._current_assistant_turn = None
        self._follow_output(follow_output)

    async def add_notice(self, text: str) -> None:
        follow_output = self.is_vertical_scroll_end
        self._finish_assistant_turn()
        await self.mount(SystemNotice(text))
        self._follow_output(follow_output)

    async def add_agent_changed(self, name: str) -> None:
        if name == self._active_agent_name:
            return

        follow_output = self.is_vertical_scroll_end
        self._finish_assistant_turn()
        self._active_agent_name = name
        await self.mount(SystemNotice(f"Agent → {name}"))
        self._follow_output(follow_output)

    async def add_reasoning(self, text: str) -> ActivityStep:
        follow_output = self.is_vertical_scroll_end
        step = ActivityStep(self._activity_detail_max_height)
        step.append_reasoning(text)
        self._recent_reasoning.append(text)
        await self._add_activity_step(step)
        self._follow_output(follow_output)
        return step

    async def add_tool_call(self, name: str, arguments: str | None) -> ActivityStep:
        follow_output = self.is_vertical_scroll_end
        step = ActivityStep(self._activity_detail_max_height)
        step.label_reasoning = "".join(self._recent_reasoning)
        self._recent_reasoning.clear()
        step.set_tool(name, arguments)
        self._pending_tool_outputs.append(step)
        self._tool_steps.append(step)
        await self._add_activity_step(step)
        self._follow_output(follow_output)
        return step

    async def add_approval(
        self,
        tool_name: str,
        arguments: str | None,
        approved: bool,
        *,
        source: str | None = None,
        review_decision: str | None = None,
        review_reason: str | None = None,
    ) -> None:
        step = next(
            (
                candidate
                for candidate in self._tool_steps
                if not candidate.approval_recorded
                and candidate.tool_name == tool_name
                and candidate.tool_arguments == arguments
            ),
            None,
        )
        if step is None:
            logger.warning("Unable to match approval to tool %s", tool_name)
            await self.add_notice("Tool action approved." if approved else "Tool action rejected.")
            return

        step.set_approval(
            approved,
            source=source,
            review_decision=review_decision,
            review_reason=review_reason,
        )

    async def add_tool_output(self, output: str) -> ActivityStep:
        follow_output = self.is_vertical_scroll_end
        if not self._pending_tool_outputs:
            logger.warning("Unable to match tool output to a pending tool call")
            await self.add_notice("Unmatched tool output received.")
            return ActivityStep(self._activity_detail_max_height, "Unmatched tool output")

        step = self._pending_tool_outputs.popleft()
        step.set_observation(output)
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
        self._finish_activity()
        self._current_assistant_turn = None
        self._active_agent_name = self._initial_agent_name
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
                tool_name = record.get("tool_name")
                arguments = record.get("arguments")
                if isinstance(tool_name, str):
                    await self.add_approval(
                        tool_name,
                        arguments if isinstance(arguments, str) else None,
                        record["approved"],
                        source=record.get("source") if isinstance(record.get("source"), str) else None,
                        review_decision=record.get("review_decision") if isinstance(record.get("review_decision"), str) else None,
                        review_reason=record.get("review_reason") if isinstance(record.get("review_reason"), str) else None,
                    )
