import asyncio
import os
import uuid
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
from qmt_agent.cli import parse_command
from qmt_agent.mcp import load_mcp_servers
from qmt_agent.storage.sessions import list_sessions, session_exists

SESSION_DB = "qmt_agent_sessions.db"

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
        uuid.uuid4().hex,
        SESSION_DB,
    )

    try:
        async with MCPServerManager(mcp_servers) as mcp_manager:

            agent = create_agent(
                model=model,
                mcp_servers=mcp_manager.active_servers,
            )

            while True:
                user_input = (
                    await asyncio.to_thread(input, "You: ")
                ).strip()

                try:
                    command = parse_command(user_input)
                except ValueError as e:
                    print(f"Invalid command: {e}")
                    continue

                if command:
                    match command.name:
                        case "session":
                            print(f"Current session ID: {session.session_id}")

                        case "new":
                            session.close()
                            session_id = uuid.uuid4().hex
                            session = SQLiteSession(
                                session_id,
                                SESSION_DB,
                            )
                            print(f"Started new session: {session_id}")

                        case "resume":
                            sessions = []
                            if not command.args:
                                sessions = (await asyncio.to_thread(
                                    list_sessions,
                                    SESSION_DB,
                                ))

                                print("Available sessions:")

                                for record in sessions:
                                    marker = ("*" if record.session_id == session.session_id else " ")
                                    print(f"{marker} {record.session_id} (updated: {record.updated_at}, created: {record.created_at})")

                                continue

                            session_id = command.args[0]
                            if (session_id == session.session_id):
                                continue

                            exists = (await asyncio.to_thread(
                                session_exists,
                                SESSION_DB,
                                session_id,
                            ))

                            if not exists:
                                print(f"Session ID {session_id} does not found.")
                                continue

                            session.close()
                            session = SQLiteSession(
                                session_id,
                                SESSION_DB,
                            )
                            print(f"Resumed session: {session_id}")

                        case "clear":
                            await session.clear_session()
                            session.close()
                            session_id = uuid.uuid4().hex
                            session = SQLiteSession(
                                session_id,
                                SESSION_DB,
                            )
                            print(f"Cleared session and started new session: {session_id}")

                        case "exit":
                            print("Exiting...")
                            break

                        case "help":
                            raise NotImplementedError("Help command is not implemented yet.")

                        case _:
                            print(f"Unknown command: /{command.name}. For help, type /help.")

                    continue

                result = await Runner.run(
                    agent,
                    user_input,
                    session=session,
                )

                print("Agent: ", result.final_output)
    finally:
        session.close()

if __name__ == "__main__":
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
    asyncio.run(main())