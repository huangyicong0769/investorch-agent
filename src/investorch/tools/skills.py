from typing import Literal

from agents import RunContextWrapper
from agents.decorators import tool

from investorch.application.skills import SkillOperations
from investorch.context import AgentContext


def _skills(ctx: RunContextWrapper[AgentContext]) -> SkillOperations:
    if ctx.context.skills is None:
        raise ValueError("Skill service is unavailable")
    return ctx.context.skills


@tool
def list_skills(ctx: RunContextWrapper[AgentContext]) -> list[dict]:
    """List registered Skills with current metadata, provenance, enabled state, and validation status."""
    return _skills(ctx).list()


@tool
def inspect_skill(ctx: RunContextWrapper[AgentContext], name: str) -> dict:
    """Inspect one registered Skill's metadata, resources and validation status without loading its body."""
    return _skills(ctx).inspect(name)


@tool
def load_skill(ctx: RunContextWrapper[AgentContext], name: str) -> dict:
    """Load a Skill from the startup catalog, including full instructions and resource inventory."""
    return _skills(ctx).load(name)


@tool
async def review_skill_candidate(
    ctx: RunContextWrapper[AgentContext], candidate_path: str, source_type: Literal["created", "external"] = "external"
) -> dict:
    """Validate and independently review a Workspace candidate; installation repeats safety review before approval."""
    result = await _skills(ctx).review_candidate(candidate_path, source_type)
    return result.review.model_dump()


@tool(needs_approval=True)
def install_skill(
    ctx: RunContextWrapper[AgentContext],
    candidate_path: str,
    source_type: Literal["created", "external"],
    origin: str | None = None,
    replace: bool = False,
) -> dict:
    """Install or explicitly replace a custom Skill after fresh safety review and approval; restart required."""
    return _skills(ctx).install(
        run_id=ctx.context.run_id,
        candidate_path=candidate_path,
        source_type=source_type,
        origin=origin,
        replace=replace,
    )


@tool(needs_approval=True)
def remove_skill(ctx: RunContextWrapper[AgentContext], name: str) -> dict:
    """Remove an external or created Skill; built-ins cannot be removed. Restart required."""
    return _skills(ctx).remove(name)


@tool(needs_approval=True)
def set_skill_enabled(ctx: RunContextWrapper[AgentContext], name: str, enabled: bool) -> dict:
    """Enable or disable a registered Skill, including built-ins; restart required."""
    return _skills(ctx).set_enabled(name, enabled)
