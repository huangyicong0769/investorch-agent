from investorch.application.brokers import BrokerOperations
from tests.support.config import make_test_config


async def test_broker_application_registers_and_reads_account_identity(tmp_path):
    ops = BrokerOperations(config=make_test_config(tmp_path))
    broker = await ops.create_broker(provider="qmt", display_name="Broker", metadata={"region": "cn"})
    account = await ops.create_broker_account(
        broker.broker_id, external_account_id="123", display_name="Trading", account_type="stock"
    )
    assert await ops.get_broker(broker.broker_id) == broker
    assert await ops.get_broker_account(account.broker_account_id) == account
    assert await ops.list_brokers() == [broker]
    assert await ops.list_broker_accounts(broker_id=broker.broker_id) == [account]
