from agents import Agent, Model, ModelSettings, TResponseInputItem

from .continuation import portable_history
from .prompts import TITLE_AGENT_INSTRUCTIONS
from .usage import TokenUsage, UsageHandler, run_with_usage


def create_title_agent(model: Model, model_settings: ModelSettings) -> Agent:
    return Agent(
        name="Session Title Agent",
        instructions=TITLE_AGENT_INSTRUCTIONS,
        model=model,
        model_settings=model_settings,
    )


async def generate_session_title(
    title_agent: Agent, history: list[TResponseInputItem], *, usage_handler: UsageHandler | None = None
) -> tuple[str, TokenUsage]:
    result = await run_with_usage(
        title_agent,
        [
            *portable_history(history),
            {
                "role": "user",
                "content": "Generate a concise title for the conversation above. **Output only the title.**",
            },
        ],
        usage_handler=usage_handler,
    )

    title = str(result.final_output).strip()

    if not title:
        raise ValueError("Title agent returned an empty title.")
    return title, TokenUsage.from_sdk(result.context_wrapper.usage)
