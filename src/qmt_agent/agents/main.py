import asyncio

from agents import Agent, OpenAIResponsesModel, Runner, set_tracing_disabled
from agents.mcp import MCPServer
from openai import AsyncOpenAI

from qmt_agent import tools
from qmt_agent.config import load_config
from qmt_agent.context import AgentContext, ExecutionState

from .prompts import MAIN_AGENT_INSTRUCTIONS


def create_agent(model: OpenAIResponsesModel, mcp_servers: list[MCPServer] | None = None) -> Agent[AgentContext]:
    return Agent[AgentContext](
        name="QMT Agent",
        instructions=MAIN_AGENT_INSTRUCTIONS,
        model=model,
        tools=[
            tools.calculate,
            tools.configure_mcp_server,
            tools.data_run,
            tools.data_status,
            tools.delete,
            tools.edit,
            tools.exec_command,
            tools.explore,
            tools.get_config,
            tools.get_current_time,
            tools.list_mcp_servers,
            tools.remove_mcp_server,
            tools.stop_background_job,
            tools.update_config,
            tools.write_todos,
        ],
        mcp_servers=mcp_servers or [],
        mcp_config={"include_server_in_tool_names": True},
    )


if __name__ == "__main__":
    set_tracing_disabled(True)

    config = load_config()

    client = AsyncOpenAI(
        api_key=config.secret("DEEPSEEK_API_KEY"),
        base_url=config["model.base_url"],
    )

    model = OpenAIResponsesModel(
        model=config["model.name"],
        openai_client=client,
    )

    agent = create_agent(model)

    user_input = input("You ")

    async def run_once():
        execution = ExecutionState()

        try:
            await tools.start_execution(execution, config.workspace_dir)
            context = AgentContext(config=config, execution=execution)
            return await Runner.run(
                agent,
                user_input,
                context=context,
            )
        finally:
            await tools.close_execution(execution)

    result = asyncio.run(run_once())

    print("Agent:", result.final_output)
