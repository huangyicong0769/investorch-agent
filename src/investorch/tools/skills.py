from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents import RunContextWrapper

from investorch.context import AgentContext
from investorch.tools.errors import tool

if TYPE_CHECKING:
    from investorch.application.skills import SkillOperations


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
    """Inspect one registered Skill's metadata, resources and validation status without loading its body.

    Args:
        name: Registered Skill name from list_skills, including disabled Skills.
    """
    return _skills(ctx).inspect(name)


@tool
def load_skill(ctx: RunContextWrapper[AgentContext], name: str) -> dict:
    """Load a Skill from the startup catalog, including full instructions and resource inventory.

    Args:
        name: Enabled Skill name from the startup catalog; restart after catalog management changes.
    """
    return _skills(ctx).load(name)


@tool
async def review_skill_candidate(
    ctx: RunContextWrapper[AgentContext], candidate_path: str, source_type: Literal["created", "external"] = "external"
) -> dict:
    """Validate and independently review a Workspace candidate; installation repeats safety review before approval.

    Use this as the first structural check. It validates UTF-8, YAML, metadata,
    and resource boundaries before safety review, returning a specific error
    on invalid content. Separate encoding checks are needed only to investigate
    a reported decoding failure.

    Args:
        candidate_path: Candidate Skill directory containing SKILL.md, inside Workspace and outside installed skills.
        source_type: "created" for an authored candidate or "external" for an imported candidate. Defaults to "external".
    """
    result = await _skills(ctx).review_candidate(
        candidate_path, source_type, usage_handler=ctx.context.auxiliary_usage_handler
    )
    return result.review.model_dump()


@tool(needs_approval=True)
def install_skill(
    ctx: RunContextWrapper[AgentContext],
    candidate_path: str,
    source_type: Literal["created", "external"],
    origin: str | None = None,
    replace: bool = False,
) -> dict:
    """Install or explicitly replace a custom Skill after fresh safety review and approval; restart required.

    Args:
        candidate_path: Candidate Skill directory containing SKILL.md, inside Workspace and outside installed skills.
        source_type: "created" for an authored candidate or "external" for an imported candidate.
        origin: Optional non-empty provenance reference for an external Skill, such as its source URL.
            Omit for created Skills. Defaults to null.
        replace: Replace an existing custom Skill with the same declared name when true. Defaults to false.
            Built-in Skills cannot be replaced; fork with a new name instead.
    """
    return _skills(ctx).install(
        run_id=ctx.context.run_id,
        candidate_path=candidate_path,
        source_type=source_type,
        origin=origin,
        replace=replace,
    )


@tool(needs_approval=True)
def remove_skill(ctx: RunContextWrapper[AgentContext], name: str) -> dict:
    """Remove an external or created Skill; built-ins cannot be removed. Restart required.

    Args:
        name: Registered external or created Skill name from list_skills.
    """
    return _skills(ctx).remove(name)


@tool(needs_approval=True)
def set_skill_enabled(ctx: RunContextWrapper[AgentContext], name: str, enabled: bool) -> dict:
    """Enable or disable a registered Skill, including built-ins; restart required.

    Args:
        name: Registered Skill name from list_skills, including built-in Skills.
        enabled: True to enable the Skill in the next startup catalog, or false to disable it.
    """
    return _skills(ctx).set_enabled(name, enabled)
