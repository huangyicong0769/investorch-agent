from typing import Any

from agents import RunContextWrapper
from agents.decorators import tool

from investorch.context import AgentContext


@tool
async def list_broker_accounts(context: RunContextWrapper[AgentContext]) -> dict[str, Any]:
    """List registered BrokerAccount identities for live deployment; does not connect to a broker."""
    from investorch.application.brokers import BrokerOperations

    operations = BrokerOperations(config=context.context.config)
    brokers = {broker.broker_id: broker for broker in await operations.list_brokers()}
    accounts = await operations.list_broker_accounts()
    return {
        "broker_accounts": [
            {
                "broker_account_id": account.broker_account_id,
                "broker_id": account.broker_id,
                "provider": brokers[account.broker_id].provider,
                "broker_display_name": brokers[account.broker_id].display_name,
                "display_name": account.display_name,
                "external_account_id": account.external_account_id,
                "account_type": account.account_type,
            }
            for account in accounts
        ]
    }


@tool(needs_approval=True)
async def deploy_live_strategy(
    context: RunContextWrapper[AgentContext], portfolio_id: str, broker_account_id: str
) -> dict[str, Any]:
    """Deploy the Portfolio's current bound strategy to a registered BrokerAccount after approval.

    A retry with an ACTIVE deployment preserves its existing frozen strategy. Deployment identities,
    source bytes, and transport details are managed internally.

    Args:
        portfolio_id: Portfolio whose current StrategyBinding should be deployed.
        broker_account_id: Registered BrokerAccount selected for execution.
    """
    coordinator = context.context.live_coordinator
    if coordinator is None:
        return {"status": "unavailable", "code": "EXECUTION_NODE_NOT_CONFIGURED"}
    return await coordinator.deploy_live_strategy(portfolio_id, broker_account_id)


@tool
async def get_live_status(context: RunContextWrapper[AgentContext], portfolio_id: str | None = None) -> dict[str, Any]:
    """Read live ownership, node availability, and synchronization without changing strategy intent.

    Args:
        portfolio_id: Portfolio to inspect, or null to summarize live-related Portfolios.
    """
    coordinator = context.context.live_coordinator
    if coordinator is None:
        return {"status": "unavailable", "code": "EXECUTION_NODE_NOT_CONFIGURED"}
    return await coordinator.get_live_status(portfolio_id)
