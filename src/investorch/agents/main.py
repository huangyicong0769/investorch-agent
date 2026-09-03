from agents import Agent, ModelSettings, OpenAIResponsesModel
from agents.mcp import MCPServer

from investorch import tools
from investorch.config import AppConfig
from investorch.context import AgentContext

from .prompts import MAIN_AGENT_INSTRUCTIONS


def create_agent(
    model: OpenAIResponsesModel,
    model_settings: ModelSettings,
    config: AppConfig,
    mcp_servers: list[MCPServer] | None = None,
) -> Agent[AgentContext]:
    agent_tools = [
        tools.calculate,
        tools.configure_mcp_server,
        tools.delete,
        tools.edit,
        tools.exec_command,
        tools.explore,
        tools.get_config,
        tools.get_current_time,
        tools.list_portfolios,
        tools.get_portfolio,
        tools.get_portfolio_ledger,
        tools.create_portfolio,
        tools.update_portfolio,
        tools.archive_portfolio,
        tools.restore_portfolio,
        tools.initialize_portfolio,
        tools.record_portfolio_trade,
        tools.record_portfolio_cash_flow,
        tools.record_portfolio_income,
        tools.adjust_portfolio_position,
        tools.adjust_portfolio_cash,
        tools.correct_portfolio_entry,
        tools.transfer_portfolio_position,
        tools.transfer_portfolio_cash,
        tools.list_mcp_servers,
        tools.remove_mcp_server,
        tools.run_backtest,
        tools.update_config,
        tools.write_todos,
    ]
    if not config["backtest.use_cnequity"]:
        agent_tools.append(tools.inspect_rqalpha_data)

    return Agent[AgentContext](
        name="InvestOrch Agent",
        instructions=MAIN_AGENT_INSTRUCTIONS,
        model=model,
        model_settings=model_settings,
        tools=agent_tools,
        mcp_servers=mcp_servers or [],
        mcp_config={"include_server_in_tool_names": config["mcp.include_server_in_tool_names"]},
    )
