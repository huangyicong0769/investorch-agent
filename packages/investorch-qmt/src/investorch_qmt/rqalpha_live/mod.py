"""The single live Mod entry point, without a production Broker or EventSource."""

import json
from dataclasses import replace

from rqalpha.const import EXIT_CODE, RUN_TYPE
from rqalpha.core.events import EVENT
from rqalpha.interface import AbstractMod

from .bootstrap import native_portfolio_inputs
from .contracts import RQAlphaLiveBootstrapSnapshot
from .state import Lifecycle, PortfolioSync, RuntimeState


class InvestOrchLiveMod(AbstractMod):
    def __init__(self):
        self.state = RuntimeState()
        self.snapshot = None

    def start_up(self, env, mod_config):
        if env.config.base.run_type is not RUN_TYPE.LIVE_TRADING or hasattr(env, "portfolio"):
            raise ValueError("live bootstrap requires a new live runtime before Portfolio construction")
        self.snapshot = RQAlphaLiveBootstrapSnapshot.from_wire(json.loads(mod_config.snapshot_json))
        accounts, positions = native_portfolio_inputs(self.snapshot)
        # main.run constructs Portfolio after Mod.start_up and data/date initialization.
        # Replacing Portfolio at POST_SYSTEM_INIT would leave duplicate accounting listeners.
        env.config.base.accounts = accounts
        env.config.base.init_positions = positions

        def verify_bootstrap(event):
            observed = {
                position.order_book_id: position.quantity
                for position in env.portfolio.get_positions()
                if position.quantity
            }
            if env.portfolio.cash != accounts["STOCK"] or observed != dict(positions):
                self.state = replace(self.state, lifecycle=Lifecycle.FAILED, portfolio_sync=PortfolioSync.DESYNCED)
                raise ValueError("native RQAlpha Portfolio does not match bootstrap snapshot")
            self.state = replace(self.state, portfolio_sync=PortfolioSync.SYNCED)

        env.event_bus.add_listener(EVENT.POST_SYSTEM_INIT, verify_bootstrap)

    def tear_down(self, code, exception=None):
        lifecycle = Lifecycle.STOPPED if code == EXIT_CODE.EXIT_SUCCESS and exception is None else Lifecycle.FAILED
        self.state = replace(self.state, lifecycle=lifecycle)


def load_mod():
    return InvestOrchLiveMod()
