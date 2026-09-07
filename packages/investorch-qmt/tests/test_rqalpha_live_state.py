from dataclasses import replace

import pytest

from investorch_qmt.rqalpha_live.state import DependencyHealth, Lifecycle, PortfolioSync, RuntimeState


def test_only_running_synced_and_known_healthy_dependencies_can_submit():
    state = RuntimeState(Lifecycle.RUNNING, PortfolioSync.SYNCED, (DependencyHealth.AVAILABLE,))
    assert state.can_submit_new_order
    assert not RuntimeState().can_submit_new_order
    assert not replace(state, required_dependencies=()).can_submit_new_order
    assert not replace(
        state, required_dependencies=(DependencyHealth.AVAILABLE, DependencyHealth.UNAVAILABLE)
    ).can_submit_new_order


@pytest.mark.parametrize("lifecycle", [Lifecycle.STARTING, Lifecycle.STOPPING, Lifecycle.STOPPED, Lifecycle.FAILED])
def test_nonrunning_lifecycle_blocks_new_orders(lifecycle):
    assert not RuntimeState(lifecycle, PortfolioSync.SYNCED, (DependencyHealth.AVAILABLE,)).can_submit_new_order


@pytest.mark.parametrize("sync", [PortfolioSync.COMMIT_PENDING, PortfolioSync.DESYNCED])
def test_unsynced_portfolio_blocks_new_orders(sync):
    assert not RuntimeState(Lifecycle.RUNNING, sync, (DependencyHealth.AVAILABLE,)).can_submit_new_order
