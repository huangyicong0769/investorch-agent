import asyncio
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
from openai import AsyncOpenAI

from qmt_agent.agents import (
    create_agent,
    create_summary_agent,
    create_title_agent,
)
from qmt_agent.cli import parse_command
from qmt_agent.config import load_config
from qmt_agent.context import AgentContext
from qmt_agent.mcp import load_mcp_servers
from qmt_agent.observability import print_run_events
from qmt_agent.storage.sessions import (
    delete_session_metadata,
    find_session_ids,
    get_session_title,
    init_session_metadata,
    list_sessions,
    set_session_title,
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


async def ensure_session_title(title_agent: Agent, session: SQLiteSession, session_db : str | Path) -> None:
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


async def main():
    config = load_config()

    client = AsyncOpenAI(
        api_key=config.secret("DEEPSEEK_API_KEY"),
        base_url=config["model.base_url"],
    )

    model = OpenAIResponsesModel(
        model=config["model.name"],
        openai_client=client,
    )

    mcp_servers = load_mcp_servers(
        config.mcp_config_path,
        variables=config.secrets,
    )

    config.state_dir.mkdir(parents=True, exist_ok=True)

    session_db = config.sessions_db

    await asyncio.to_thread(
        init_session_metadata,
        session_db,
    )

    session = SQLiteSession(
        uuid.uuid4().hex,
        session_db,
    )

    title_agent = create_title_agent(model)
    summary_agent = create_summary_agent(model)

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
                                session_db,
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
                                session_db,
                            )
                            print(f"Started new session: {session_id}")

                        case "resume":
                            sessions = []
                            if not command.args:
                                sessions = (await asyncio.to_thread(
                                    list_sessions,
                                    session_db,
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
                                session_db,
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
                                session_db,
                            )

                            title = await asyncio.to_thread(
                                get_session_title,
                                session_db,
                                session_id,
                            )
                            print(f"Resumed session: {session_id}")
                            if title:
                                print(f"Session title: {title}")

                        case "title":
                            if not command.args:
                                title = await asyncio.to_thread(
                                    get_session_title,
                                    session_db,
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
                                session_db,
                                session.session_id,
                                title,
                            )
                            print(f"Set session title to: {title}")

                        case "clear":
                            session_id = session.session_id

                            await session.clear_session()
                            await asyncio.to_thread(
                                delete_session_metadata,
                                session_db,
                                session_id,
                            )

                            session.close()
                            session_id = uuid.uuid4().hex
                            session = SQLiteSession(
                                session_id,
                                session_db,
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

                result = Runner.run_streamed(
                    agent,
                    user_input,
                    session=session,
                    context=AgentContext(config=config),
                )

                await print_run_events(
                    result,
                    summary_agent=summary_agent,
                )

                print("Agent: ", result.final_output)

                await ensure_session_title(
                    title_agent=title_agent,
                    session=session,
                    session_db=session_db,
                )
    finally:
        session.close()

if __name__ == "__main__":
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
    asyncio.run(main())