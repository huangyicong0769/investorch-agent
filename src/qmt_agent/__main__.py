import asyncio
import os
import uuid
from pathlib import Path

from agents import (
    Agent,
    OpenAIResponsesModel,
    Runner,
    SQLiteSession,
    TResponseInputItem,
)
from agents.mcp import MCPServerManager
from dotenv import load_dotenv
from openai import AsyncOpenAI

from qmt_agent.agents import create_agent, create_title_agent
from qmt_agent.cli import parse_command
from qmt_agent.mcp import load_mcp_servers
from qmt_agent.storage.sessions import (
    delete_session_metadata,
    find_session_ids,
    get_session_title,
    init_session_metadata,
    list_sessions,
    set_session_title,
)

SESSION_DB = "qmt_agent_sessions.db"

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


async def ensure_session_title(title_agent: Agent, session: SQLiteSession) -> None:
    existing_title = await asyncio.to_thread(
        get_session_title,
        SESSION_DB,
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
        SESSION_DB,
        session.session_id,
        title,
    )


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

    await asyncio.to_thread(
        init_session_metadata,
        SESSION_DB,
    )

    session = SQLiteSession(
        uuid.uuid4().hex,
        SESSION_DB,
    )

    title_agent = create_title_agent(model)

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
                            title = await asyncio.to_thread(
                                get_session_title,
                                SESSION_DB,
                                session.session_id,
                            )
                            print(f"Current session ID: {session.session_id}")
                            if title:
                                print(f"Session title: {title}")

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
                                    title = record.title or "(untitled)"
                                    print(f"{marker} {record.session_id[:8]} {title}, (updated: {record.updated_at}, created: {record.created_at})")

                                continue

                            session_id = command.args[0]

                            matches = await asyncio.to_thread(
                                find_session_ids,
                                SESSION_DB,
                                session_id,
                            )

                            if not matches:
                                print(f"Session ID {session_id} not found.")
                                continue

                            if len(matches) > 1:
                                print(f"Multiple sessions found with prefix {session_id}:")
                                for match in matches:
                                    print(f"  {match}")
                                continue

                            session_id = matches[0]
                            if (session_id == session.session_id):
                                continue

                            session.close()
                            session = SQLiteSession(
                                session_id,
                                SESSION_DB,
                            )

                            title = await asyncio.to_thread(
                                get_session_title,
                                SESSION_DB,
                                session_id,
                            )
                            print(f"Resumed session: {session_id}")
                            if title:
                                print(f"Session title: {title}")

                        case "title":
                            if not command.args:
                                title = await asyncio.to_thread(
                                    get_session_title,
                                    SESSION_DB,
                                    session.session_id,
                                )
                                if title:
                                    print(f"Session title: {title}")
                                else:
                                    print("Session has no title.")
                                continue

                            title = " ".join(command.args).strip()

                            await asyncio.to_thread(
                                set_session_title,
                                SESSION_DB,
                                session.session_id,
                                title,
                            )
                            print(f"Set session title to: {title}")

                        case "clear":
                            session_id = session.session_id

                            await session.clear_session()
                            await asyncio.to_thread(
                                delete_session_metadata,
                                SESSION_DB,
                                session_id,
                            )

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

                await ensure_session_title(
                    title_agent=title_agent,
                    session=session,
                )
    finally:
        session.close()

if __name__ == "__main__":
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
    asyncio.run(main())