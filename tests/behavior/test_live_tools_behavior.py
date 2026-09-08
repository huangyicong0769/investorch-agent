from pathlib import Path

from agents.tool_context import ToolContext

from investorch.application.brokers import BrokerOperations
from investorch.application.portfolios import PortfolioOperations
from investorch.context import AgentContext, ExecutionState
from investorch.tools.live import list_broker_accounts
from tests.support.config import make_test_config


async def test_broker_discovery_exposes_account_identity_without_private_metadata(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    brokers = BrokerOperations(config=config)
    broker = await brokers.create_broker(provider="qmt", display_name="Node", metadata={"private": "hidden"})
    account = await brokers.create_broker_account(
        broker.broker_id,
        external_account_id="A123",
        display_name="Trading",
        account_type="stock",
        metadata={"private": "hidden"},
    )
    context = ToolContext(
        AgentContext(config, ExecutionState(), "session", "run", PortfolioOperations(config=config)),
        tool_name="list_broker_accounts",
        tool_call_id="call",
        tool_arguments="{}",
    )
    result = await list_broker_accounts.on_invoke_tool(context, "{}")
    assert result == {
        "broker_accounts": [
            {
                "broker_account_id": account.broker_account_id,
                "broker_id": broker.broker_id,
                "provider": "qmt",
                "broker_display_name": "Node",
                "display_name": "Trading",
                "external_account_id": "A123",
                "account_type": "stock",
            }
        ]
    }


async def test_deploy_runs_only_after_approval_and_receives_runtime_coordinator(tmp_path: Path) -> None:
    from agents import Agent, SQLiteSession
    from agents.testing import ScriptedModel, assistant_message, function_call

    from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
    from investorch.runtime.control import RunControl
    from investorch.tools.live import deploy_live_strategy

    config = make_test_config(tmp_path)
    calls = []

    class Coordinator:
        async def deploy_live_strategy(self, portfolio_id, broker_account_id):
            calls.append((portfolio_id, broker_account_id))
            return {"status": "staged"}

    for approve in (False, True):
        model = ScriptedModel(
            (
                (
                    function_call(
                        "deploy_live_strategy", {"portfolio_id": "p", "broker_account_id": "a"}, call_id="deploy"
                    ),
                ),
                (assistant_message("done"),),
            )
        )
        agent = Agent[AgentContext](name="Main", instructions="test", model=model, tools=[deploy_live_strategy])
        unused = Agent(name="Unused", model=ScriptedModel())
        loop = AgentLoop(
            agent, unused, unused, config, PortfolioOperations(config=config), live_coordinator=Coordinator()
        )
        session = SQLiteSession(f"approval-{approve}", config.sessions_db)

        async def approval_handler(*args, approved=approve):
            assert calls == []
            return ApprovalOutcome(approved=approved, usage=TokenUsage())

        async def output_handler(event):
            pass

        try:
            await loop.run(
                "deploy",
                session,
                ExecutionState(),
                run_id="run",
                session_id=f"approval-{approve}",
                reasoning_effort="none",
                approval_handler=approval_handler,
                output_handler=output_handler,
                run_control=RunControl(f"approval-{approve}", "run", lambda: None),
            )
        finally:
            session.close()
        model.assert_complete()
        assert calls == ([("p", "a")] if approve else [])


async def test_live_status_is_read_only_and_preserves_coordinator_truth(tmp_path: Path) -> None:
    from investorch.tools.live import get_live_status

    config = make_test_config(tmp_path)

    class Coordinator:
        async def get_live_status(self, portfolio_id):
            return {"portfolio_id": portfolio_id, "sync": "DESYNCED", "qmt": {"status": "not_connected"}}

    context = ToolContext(
        AgentContext(
            config,
            ExecutionState(),
            "session",
            "run",
            PortfolioOperations(config=config),
            live_coordinator=Coordinator(),
        ),
        tool_name="get_live_status",
        tool_call_id="status",
        tool_arguments='{"portfolio_id":"p"}',
    )
    assert get_live_status.needs_approval is False
    assert await get_live_status.on_invoke_tool(context, '{"portfolio_id":"p"}') == {
        "portfolio_id": "p",
        "sync": "DESYNCED",
        "qmt": {"status": "not_connected"},
    }


async def test_unconfigured_live_tools_report_unavailable_without_touching_portfolios(tmp_path: Path) -> None:
    from investorch.tools.live import deploy_live_strategy, get_live_status

    config = make_test_config(tmp_path)
    context = AgentContext(config, ExecutionState(), "session", "run", PortfolioOperations(config=config))
    for tool, arguments in [
        (get_live_status, '{"portfolio_id":null}'),
        (deploy_live_strategy, '{"portfolio_id":"missing", "broker_account_id":"missing"}'),
    ]:
        wrapper = ToolContext(context, tool_name=tool.name, tool_call_id="call", tool_arguments=arguments)
        assert await tool.on_invoke_tool(wrapper, arguments) == {
            "status": "unavailable",
            "code": "EXECUTION_NODE_NOT_CONFIGURED",
        }
