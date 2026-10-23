from dataclasses import dataclass
from typing import Literal

from agents import Agent, ModelSettings
from pydantic import BaseModel

from .prompts import SKILL_REVIEW_INSTRUCTIONS
from .usage import TokenUsage, UsageHandler, run_with_usage


class SkillSafetyReview(BaseModel):
    decision: Literal["pass", "warn", "block"]
    reason: str


@dataclass(frozen=True, slots=True)
class SkillReviewResult:
    review: SkillSafetyReview
    usage: TokenUsage


def create_skill_review_agent(model, model_settings: ModelSettings) -> Agent:
    return Agent(
        name="Skill Safety Review Agent",
        instructions=SKILL_REVIEW_INSTRUCTIONS,
        model=model,
        model_settings=model_settings,
        output_type=SkillSafetyReview,
        tools=[],
        mcp_servers=[],
    )


async def review_skill(
    agent: Agent, candidate_input: str, *, usage_handler: UsageHandler | None = None
) -> SkillReviewResult:
    result = await run_with_usage(agent, candidate_input, usage_handler=usage_handler)
    review = result.final_output
    if not isinstance(review, SkillSafetyReview):
        raise ValueError("Skill Review Agent returned invalid structured output")
    reason = review.reason.strip()
    if not reason or len(reason) > 2000:
        raise ValueError("Skill Review Agent returned an empty or oversized reason")
    return SkillReviewResult(
        SkillSafetyReview(decision=review.decision, reason=reason), TokenUsage.from_sdk(result.context_wrapper.usage)
    )
