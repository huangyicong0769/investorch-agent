import asyncio
import os
from pathlib import Path

from agents import (
    OpenAIResponsesModel,
    Runner,
    SQLiteSession,
)
from agents.mcp import MCPServerManager
from dotenv import load_dotenv
from openai import AsyncOpenAI

from qmt_agent.agents.main import create_agent
from qmt_agent.mcp import load_mcp_servers


async def main():
    load_dotenv()

    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ["DEEPSEEK_BASE_URL"],
    )

    model = OpenAIResponsesModel(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        openai_client=client,
    )

    mcp_servers = load_mcp_servers(
        Path("config/mcp.toml")
    )

    session = SQLiteSession(
        "local_cli",
        "qmt_agent_sessions.db",
    )

    async with MCPServerManager(mcp_servers, strict=True) as mcp_manager:

        agent = create_agent(
            model=model,
            mcp_servers=mcp_manager.active_servers,
        )

        while True:
            user_input = (
                await asyncio.to_thread(input, "You: ")
            ).strip()

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