import asyncio
import os
import sys
import uuid
from pathlib import Path

from agents import OpenAIResponsesModel, SQLiteSession, set_tracing_disabled
from agents.mcp import MCPServerManager, MCPServerStdio
from openai import AsyncOpenAI

from qmt_agent.agents import (
    AgentLoop,
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
from qmt_agent.initializer import initialize, sync_bootstrap_files
from qmt_agent.mcp import load_mcp_servers
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


def run_data_cli(args: list[str]) -> None:
    config = load_config()
    config.root.mkdir(parents=True, exist_ok=True)
    os.chdir(config.root)
    os.execv(sys.executable, [sys.executable, "-m", "cnequity", *args])


def _ask_tool_approval_sync(tool_name: str, arguments: str | None) -> bool:
    print(f"\n[approval] {tool_name}")

    if arguments:
        print(arguments)

    answer = input("Approve? [y/N]: ").strip().lower()

    return answer in {"y", "yes"}


async def ask_tool_approval(tool_name: str, arguments: str | None) -> bool:
    return await asyncio.to_thread(_ask_tool_approval_sync, tool_name, arguments)


async def main(sync: bool = False, sync_force: bool = False):
    config = load_config()

    initialized = initialize(config, copy_bootstrap=not (sync or sync_force))
    if initialized and not sync_force:
        print(
            f"QMT Agent initialized at {config.root}\n"
            f"Please configure required secrets in {config.root_config_path} and start QMT Agent again."
        )
        return

    if sync_force:
        result = await sync_bootstrap_files(config, force=True)
        backup = result.backup_dir or "none"
        print(f"Bootstrap files force-synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}")
        if initialized:
            print(f"QMT Agent initialized at {config.root}\nPlease configure required secrets in {config.root_config_path} before starting QMT Agent.")
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

    cnequity_config = (config.root / "configs" / "cnequity.toml").resolve()
    cnequity_server = MCPServerStdio(
        name="cnequity",
        params={"command": sys.executable, "args": ["-m", "cnequity", "mcp", "--config", str(cnequity_config)], "cwd": str(config.root)},
        cache_tools_list=True,
        client_session_timeout_seconds=config["mcp.default_timeout_seconds"],
    )
    mcp_servers = [cnequity_server, *load_mcp_servers(config.mcp_config_path, config.secrets, config["mcp.default_timeout_seconds"])]

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
                config=config,
                mcp_servers=mcp_manager.active_servers,
            )
            agent_loop = AgentLoop(agent, summary_agent, title_agent, config, ask_tool_approval)

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
                            jobs = await list_background_jobs(execution)
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

                output = await agent_loop.run(user_input, session, execution)
                print("Agent: ", output)
    finally:
        try:
            await close_execution(execution)
        finally:
            session.close()

def entrypoint() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "data":
        run_data_cli(sys.argv[2:])
        return

    startup_options = parse_startup_args()
    set_tracing_disabled(True)
    asyncio.run(main(sync=startup_options.sync, sync_force=startup_options.sync_force))


if __name__ == "__main__":
    entrypoint()
