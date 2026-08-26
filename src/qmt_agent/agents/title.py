import asyncio
from pathlib import Path

from agents import Agent, OpenAIResponsesModel, Runner, SQLiteSession, TResponseInputItem

from qmt_agent.storage import get_session_title, set_session_title

from .prompts import TITLE_AGENT_INSTRUCTIONS


def create_title_agent(model: OpenAIResponsesModel,) -> Agent:
    return Agent(
        name="Session Title Agent",
        instructions=TITLE_AGENT_INSTRUCTIONS,
        model=model,
    )


async def generate_session_title(title_agent: Agent, history: list[TResponseInputItem]) -> str:
    result = await Runner.run(
        title_agent,
        [
            *history,
            {
                "role": "user",
                "content": "Generate a concise title for the conversation above. **Output only the title.**",
            }
        ],
    )

    title = str(result.final_output).strip()

    if not title:
        raise ValueError("Title agent returned an empty title.")
    return title


async def ensure_session_title(title_agent: Agent, session: SQLiteSession, session_db: str | Path) -> None:
    existing_title = await asyncio.to_thread(
        get_session_title,
        session_db,
        session.session_id,
    )

    if existing_title and existing_title.strip():
        return

    history = await session.get_items()

    try:
        title = await generate_session_title(title_agent, history)
    except Exception as e:
        print(f"Failed to generate session title: {e}")
        return

    await asyncio.to_thread(
        set_session_title,
        session_db,
        session.session_id,
        title,
    )
