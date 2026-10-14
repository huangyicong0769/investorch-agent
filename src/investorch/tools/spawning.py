import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from agents import FunctionTool
from agents.tool_context import ToolContext

from investorch.context import AgentContext
from investorch.tools.errors import tool

if TYPE_CHECKING:
    from investorch.application.spawning import SpawnReceipt


def make_spawning_tools(*, submit: Callable[..., Awaitable["SpawnReceipt"]]) -> list[FunctionTool]:
    @tool(needs_approval=True)
    async def spawn_agent(context: ToolContext[AgentContext], task: str, title: str | None = None) -> dict[str, object]:
        """Delegate an approved task to a new, ordinary Agent Session in this Root.

        The child has the same model, Tools, Skills and independent Permission review, with separate
        history and Run lifecycle. Related Portfolio IDs are copied once, without authorization.
        Recursive delegation shares the Host's global Run limit, including your own Run. A receipt
        confirms input submission only; use list_sessions/read_session to observe later facts.
        Do not automatically reply or stop children when your own Run ends.

        Args:
            task: Non-empty initial user-role task text, within the configured instruction limit.
            title: Optional explicit Session title, trimmed and limited to 200 characters. Omit for automatic naming.
        """
        if set(json.loads(context.tool_arguments)) - {"task", "title"}:
            raise ValueError("spawn_agent accepts only task and title")
        receipt = await submit(
            source_session_id=context.context.session_id,
            source_run_id=context.context.run_id,
            tool_call_id=context.tool_call_id,
            task=task,
            title=title,
        )
        return {
            "session_id": receipt.session_id,
            "run_id": receipt.run_id,
            "started": receipt.started,
            "disposition": receipt.disposition,
        }

    return [spawn_agent]
