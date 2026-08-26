from __future__ import annotations

import json
from pathlib import Path

from agents import Agent, OpenAIResponsesModel, Runner

from qmt_agent import tools
from qmt_agent.context import AgentContext

from .prompts import BOOTSTRAP_SYNC_INSTRUCTIONS


def create_bootstrap_sync_agent(model: OpenAIResponsesModel) -> Agent[AgentContext]:
    return Agent[AgentContext](
        name="QMT Bootstrap Sync Agent",
        instructions=BOOTSTRAP_SYNC_INSTRUCTIONS,
        model=model,
        tools=[tools.explore, tools.edit],
    )


async def run_bootstrap_sync(
    agent: Agent[AgentContext],
    context: AgentContext,
    prompt: str,
    target: Path,
) -> None:
    result = await Runner.run(
        agent,
        prompt,
        context=context,
        run_config={"tracing_disabled": not context.config["observability.sdk_tracing_enabled"]},
    )

    while result.interruptions:
        state = result.to_state()

        for interruption in result.interruptions:
            _validate_bootstrap_interruption(interruption, context, target)

        for interruption in result.interruptions:
            state.approve(interruption, always_approve=False)

        result = await Runner.run(
            agent,
            state,
            context=context,
            run_config={"tracing_disabled": not context.config["observability.sdk_tracing_enabled"]},
        )


def _validate_bootstrap_interruption(interruption: object, context: AgentContext, target: Path) -> None:
    name = getattr(interruption, "name", None)
    if name != "edit":
        raise ValueError(f"Bootstrap sync rejected tool: {name}")

    arguments = getattr(interruption, "arguments", None)

    if not isinstance(arguments, str):
        raise TypeError("Bootstrap sync edit arguments must be JSON")

    try:
        data = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Bootstrap sync edit arguments are invalid JSON") from exc

    if not isinstance(data, dict) or not isinstance(data.get("path"), str):
        raise TypeError("Bootstrap sync edit path is invalid")

    resolved = _resolve_bootstrap_path(context.config.workspace_dir, data["path"])
    if resolved != target.resolve():
        raise RuntimeError(f"Bootstrap sync edit target is not allowed: {data['path']}")


def _resolve_bootstrap_path(workspace: Path, path: str) -> Path:
    relative = Path(path)

    if relative.is_absolute():
        raise RuntimeError("Bootstrap sync edit path must be relative")

    root = workspace.expanduser().resolve()
    resolved = (root / relative).resolve()

    if not resolved.is_relative_to(root):
        raise RuntimeError(f"Bootstrap sync edit path escapes workspace: {path}")

    return resolved
