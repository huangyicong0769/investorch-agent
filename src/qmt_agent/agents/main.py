from agents import Agent, OpenAIResponsesModel
from agents.mcp import MCPServer

from qmt_agent import tools
from qmt_agent.context import AgentContext

from .prompts import MAIN_AGENT_INSTRUCTIONS


def create_agent(model: OpenAIResponsesModel, mcp_servers: list[MCPServer] | None = None) -> Agent[AgentContext]:
    return Agent[AgentContext](
        name="QMT Agent",
        instructions=MAIN_AGENT_INSTRUCTIONS,
        model=model,
        tools=[
            tools.configure_mcp_server,
            tools.delete,
            tools.edit,
            tools.explore,
            tools.get_config,
            tools.get_current_time,
            tools.list_mcp_servers,
            tools.remove_mcp_server,
            tools.update_config,
            tools.write_todos,
        ],
        mcp_servers=mcp_servers or [],
    )


if __name__ == "__main__":
    from agents import Runner, set_tracing_disabled
    from openai import AsyncOpenAI

    from qmt_agent.config import load_config

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

    result = Runner.run_sync(
        agent,
        user_input,
        context=AgentContext(config=config),
    )

    print("Agent:", result.final_output)
