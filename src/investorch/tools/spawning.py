import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated

from agents import FunctionTool
from agents.tool_context import ToolContext
from pydantic import Field

from investorch.context import AgentContext
from investorch.tools.errors import tool

if TYPE_CHECKING:
    from investorch.application.spawning import SpawnReceipt


def make_spawning_tools(
    *, submit: Callable[..., Awaitable["SpawnReceipt"]], wait: Callable[..., Awaitable[dict[str, object]]]
) -> list[FunctionTool]:
    @tool(needs_approval=True)
    async def spawn_agent(
        context: ToolContext[AgentContext],
        task: str,
        title: str | None = None,
        model: str | None = None,
        name: str | None = None,
        reasoning_effort: str | None = None,
        context_window_tokens: int | None = None,
    ) -> dict[str, object]:
        """Delegate an approved task to a new, ordinary Agent Session in this Root.

        The child inherits this Run's model unless explicitly overridden, Tools, Skills and independent Permission review, with separate
        history and Run lifecycle. Related Portfolio IDs are copied once, without authorization.
        Recursive delegation shares the Host's global Run limit, including your own Run. A receipt
        confirms input submission only. Use wait_agents in a later tool round to wait for this Run's children.
        Use read_session to read their results.
        Do not automatically reply or stop children when your own Run ends.

        Args:
            task: Non-empty initial user-role task text, within the configured instruction limit.
            title: Optional explicit Session title, trimmed and limited to 200 characters. Omit for automatic naming.
            model: Registered connection ID. Changing connections requires name, effort and window.
            name: Actual model ID. Changing it requires an explicit context window.
            reasoning_effort: Override the inherited reasoning effort.
            context_window_tokens: Positive context window, required for a changed model name or connection.
        """
        if set(json.loads(context.tool_arguments)) - {
            "task",
            "title",
            "model",
            "name",
            "reasoning_effort",
            "context_window_tokens",
        }:
            raise ValueError(
                "spawn_agent accepts only task, title, model, name, reasoning_effort and context_window_tokens"
            )
        receipt = await submit(
            source_session_id=context.context.session_id,
            source_run_id=context.context.run_id,
            tool_call_id=context.tool_call_id,
            task=task,
            title=title,
            model=model,
            name=name,
            reasoning_effort=reasoning_effort,
            context_window_tokens=context_window_tokens,
        )
        return {
            "session_id": receipt.session_id,
            "run_id": receipt.run_id,
            "started": receipt.started,
            "disposition": receipt.disposition,
        }

    @tool
    async def wait_agents(
        context: ToolContext[AgentContext], time: Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    ) -> dict[str, object]:
        """Wait for any running direct child of this Run to finish, then return all tracked child statuses.

        Call after receiving spawn_agent's receipt. An empty child list means this Run has no tracked children.
        A timeout leaves the children running. Use read_session with a returned Session ID to read the result.

        Args:
            time: Maximum wait in seconds. Zero reads the current statuses.
        """
        return await wait(
            source_session_id=context.context.session_id, source_run_id=context.context.run_id, seconds=time
        )

    return [spawn_agent, wait_agents]
