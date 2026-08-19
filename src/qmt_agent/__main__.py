import asyncio
import uuid
from pathlib import Path

from agents import (
    Agent,
    OpenAIResponsesModel,
    Runner,
    SQLiteSession,
    TResponseInputItem,
    set_tracing_disabled,
)
from agents.mcp import MCPServerManager
from openai import AsyncOpenAI

from qmt_agent.agents import (
    build_bootstrap_sync_prompt,
    create_agent,
    create_bootstrap_sync_agent,
    create_summary_agent,
    create_title_agent,
    run_bootstrap_sync,
)
from qmt_agent.cli import parse_command, parse_startup_args
from qmt_agent.config import load_config
from qmt_agent.context import AgentContext, ExecutionState
from qmt_agent.data import load_query_servers
from qmt_agent.initializer import initialize, sync_bootstrap_files
from qmt_agent.mcp import load_mcp_servers
from qmt_agent.observability import print_run_events
from qmt_agent.storage import (
    delete_session_metadata,
    find_session_ids,
    get_session_title,
    list_sessions,
    set_session_title,
)
from qmt_agent.tools import (
    close_execution,
    format_background_jobs,
    list_background_jobs,
    start_execution,
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


def ask_tool_approval(tool_name: str, arguments: str | None) -> bool:
    print(f"\n[approval] {tool_name}")

    if arguments:
        print(arguments)

    answer = input("Approve? [y/N]: ").strip().lower()

    return answer in {"y", "yes"}


async def main(sync: bool = False):
    config = load_config()

    if initialize(config, copy_bootstrap=not sync):
        print(
            f"QMT Agent initialized at {config.root}\n"
            f"Please configure required secrets in {config.root_config_path} and start QMT Agent again."
        )
        return

    if sync:
        client = AsyncOpenAI(
            api_key=config.secret("DEEPSEEK_API_KEY"),
            base_url=config["model.base_url"],
        )

        model = OpenAIResponsesModel(
            model=config["model.name"],
            openai_client=client,
        )

        agent = create_bootstrap_sync_agent(model)

        async def merge_target(target: Path, template: str, exists: bool) -> None:
            context = AgentContext(config=config, execution=ExecutionState())
            prompt = build_bootstrap_sync_prompt(target, config.workspace_dir, template, exists)
            await run_bootstrap_sync(agent, context, prompt, target)

        result = await sync_bootstrap_files(config, merge_target)
        backup = result.backup_dir or "none"
        print(f"Bootstrap files synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}")
        return

    client = AsyncOpenAI(
        api_key=config.secret("DEEPSEEK_API_KEY"),
        base_url=config["model.base_url"],
    )

    model = OpenAIResponsesModel(
        model=config["model.name"],
        openai_client=client,
    )

    mcp_servers = [*load_query_servers(config.root, config["mcp.default_timeout_seconds"]), *load_mcp_servers(config.mcp_config_path, config.secrets, config["mcp.default_timeout_seconds"])]

    session_db = config.sessions_db

    session = SQLiteSession(
        uuid.uuid4().hex,
        session_db,
    )

    title_agent = create_title_agent(model)
    summary_agent = create_summary_agent(model)
    execution = ExecutionState()

    try:
        await start_execution(execution, config.workspace_dir)

        async with MCPServerManager(mcp_servers, drop_failed_servers=True) as mcp_manager:

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
                            session = SQLiteSession(session_id, session_db)
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
                            session = SQLiteSession(session_id, session_db)

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

                        case "ps":
                            jobs = await list_background_jobs(execution, config)
                            print(format_background_jobs(jobs))

                        case "exit":
                            print("Exiting...")
                            break

                        case "help":
                            print(
                                "Commands:\n"
                                "  /help              Show this help.\n"
                                "  /session           Show the current session.\n"
                                "  /new               Start a new session.\n"
                                "  /resume [prefix]   List or resume a session.\n"
                                "  /title [title]     Show or set the session title.\n"
                                "  /clear             Clear the current session.\n"
                                "  /ps                Show background commands.\n"
                                "  /exit              Exit QMT Agent."
                            )

                        case _:
                            print(f"Unknown command: /{command.name}. For help, type /help.")

                    continue

                agent_context = AgentContext(config=config, execution=execution)
                result = Runner.run_streamed(
                    agent,
                    user_input,
                    session=session,
                    context=agent_context,
                )

                while True:
                    await print_run_events(
                        result,
                        summary_agent,
                        config["observability.summary_enabled"],
                        config["observability.summary_threshold"],
                    )

                    if not result.interruptions:
                        break

                    state = result.to_state()

                    for interruption in result.interruptions:
                        approved = await asyncio.to_thread(
                            ask_tool_approval,
                            interruption.name or "unknown_tool",
                            interruption.arguments,
                        )

                        if approved:
                            state.approve(interruption, always_approve=False)
                        else:
                            state.reject(interruption, rejection_message=("The user rejected this tool action."))

                    result = Runner.run_streamed(
                        agent,
                        state,
                        session=session,
                    )

                print("Agent: ", result.final_output)

                await ensure_session_title(
                    title_agent=title_agent,
                    session=session,
                    session_db=session_db,
                )
    finally:
        try:
            await close_execution(execution)
        finally:
            session.close()

if __name__ == "__main__":
    startup_options = parse_startup_args()
    set_tracing_disabled(True)
    asyncio.run(main(sync=startup_options.sync))
