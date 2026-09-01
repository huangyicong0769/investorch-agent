import asyncio
import logging
from pathlib import Path

from agents import (
    Agent,
    ModelSettings,
    OpenAIResponsesModel,
    Runner,
    SQLiteSession,
    TResponseInputItem,
)

from qmt_agent.storage import get_session_title, set_session_title

from .prompts import TITLE_AGENT_INSTRUCTIONS
from .usage import TokenUsage

logger = logging.getLogger(__name__)


def create_title_agent(model: OpenAIResponsesModel, model_settings: ModelSettings) -> Agent:
    return Agent(
        name="Session Title Agent",
        instructions=TITLE_AGENT_INSTRUCTIONS,
        model=model,
        model_settings=model_settings,
    )


async def generate_session_title(title_agent: Agent, history: list[TResponseInputItem]) -> tuple[str, TokenUsage]:
    result = await Runner.run(
        title_agent,
        [
            *history,
            {
                "role": "user",
                "content": "Generate a concise title for the conversation above. **Output only the title.**",
            },
        ],
    )

    title = str(result.final_output).strip()

    if not title:
        raise ValueError("Title agent returned an empty title.")
    return title, TokenUsage.from_sdk(result.context_wrapper.usage)


async def ensure_session_title(title_agent: Agent, session: SQLiteSession, session_db: str | Path) -> TokenUsage:
    existing_title = await asyncio.to_thread(
        get_session_title,
        session_db,
        session.session_id,
    )

    if existing_title and existing_title.strip():
        return TokenUsage()

    history = await session.get_items()

    try:
        title, usage = await generate_session_title(title_agent, history)
    except Exception as e:
        logger.warning("Failed to generate session title: %s", e)
        return TokenUsage()

    await asyncio.to_thread(
        set_session_title,
        session_db,
        session.session_id,
        title,
    )
    return usage
