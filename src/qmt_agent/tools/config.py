from typing import Any

from agents import RunContextWrapper
from agents.decorators import tool

from qmt_agent.context import AgentContext


@tool
def get_config(context: RunContextWrapper[AgentContext], key: str | None = None) -> Any:
    """
    Read the current effective application configuration.

    Secret values are always redacted.

    Args:
        key:
            Optional dotted config key, for example
            "main_model.name" or "runtime.max_turns".

            If omitted, return the complete effective
            configuration.
    """
    config = context.context.config

    if key is None:
        return config.public()

    return {
        "key": key,
        "value": config.get(key),
    }


@tool(needs_approval=True)
def update_config(context: RunContextWrapper[AgentContext], key: str, value: str | bool | int | float, persist: bool = True) -> dict[str, Any]:
    """
    Update an application configuration value.

    This operation always requires user approval.

    Args:
        key:
            Dotted config key, for example
            "main_model.name" or "runtime.max_turns".

        value:
            New string, boolean, integer, or floating-point value.

        persist:
            If true, persist the override to root/qmt.toml.
            If false, only modify the current process config.
    """
    if key.startswith("secrets."):
        raise ValueError("Secrets cannot be modified through the Agent config tool.")

    return context.context.config.update(
        key,
        value,
        persist=persist,
    )
