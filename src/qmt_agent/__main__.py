import asyncio
import os

from agents import (
    OpenAIResponsesModel,
    Runner,
    SQLiteSession,
)
from dotenv import load_dotenv
from openai import AsyncOpenAI

from qmt_agent.agents.main import create_agent


async def main():
    load_dotenv()

    client = AsyncOpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        base_url=os.environ.get("DEEPSEEK_BASE_URL"),
    )

    model = OpenAIResponsesModel(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        openai_client=client,
    )

    agent = create_agent(model=model)

    session = SQLiteSession(
        "local_cli",
        "qmt_agent_sessions.db",
    )

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() in ["exit", "quit"]:
            print("Exiting...")
            break

        result = await Runner.run(
            agent,
            user_input,
            session=session,
        )

        print("Agent: ", result.final_output)

if __name__ == "__main__":
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
    asyncio.run(main())