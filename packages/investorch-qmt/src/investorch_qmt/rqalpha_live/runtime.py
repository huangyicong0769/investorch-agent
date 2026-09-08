"""Compose RQAlpha 6.3.0's real engine for an open-ended stock session stream."""

from datetime import datetime, time
from pathlib import Path

from rqalpha.const import EXECUTION_PHASE, EXIT_CODE
from rqalpha.core.events import EVENT, Event
from rqalpha.core.execution_context import ExecutionContext
from rqalpha.core.executor import Executor
from rqalpha.core.strategy import Strategy
from rqalpha.core.strategy_context import StrategyContext
from rqalpha.core.strategy_loader import SourceCodeStrategyLoader
from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.environment import Environment
from rqalpha.main import cleanup_resources, create_base_scope, get_strategy_apis, set_loggers
from rqalpha.mod import ModHandler
from rqalpha.model.bar import BarMap
from rqalpha.portfolio import Portfolio
from rqalpha.utils.config import parse_config
from rqalpha.utils.exception import CustomException

from investorch_qmt.market_data.errors import MarketDataError
from investorch_qmt.market_data.subscription import SubscriptionManager
from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter
from investorch_qmt.runtime.model import RuntimeFailure

from .bootstrap import build_live_config, native_portfolio_inputs
from .broker import TradingUnavailableBroker
from .contracts import RQAlphaLiveBootstrapSnapshot
from .data_proxy import LiveDataProxy
from .event_source import DailyEventSource, WallClock
from .price_board import LivePriceBoard


class LiveExecutor(Executor):
    def run(self, bar_dict):
        # Upstream run() clamps/defer-settles finite backtests. Keep its native event
        # splitter, but let the live event source own every daily boundary exactly once.
        config = self._env.config.base
        for event in self._env.event_source.events(config.start_date, None, "1d"):
            if event.event_type == EVENT.BAR:
                bar_dict.update_dt(event.calendar_dt)
                event.bar_dict = bar_dict
            self._split_and_publish(event)


def _config(artifacts, clock):
    overrides = build_live_config(artifacts.bootstrap, artifacts.manifest["strategy_parameters"])
    today = clock.now().date()
    overrides["base"].update(
        start_date=today.isoformat(),
        end_date=today.isoformat(),
        frequency="1d",
        persist=False,
        data_bundle_path=str(Path.home() / ".rqalpha" / "bundle"),
        auto_update_bundle=False,
        strategy_file=str(artifacts.deployment_dir / "strategy.py"),
        accounts={"stock": 1},
        rqdatac_uri="disabled",
    )
    overrides["mod"].update(
        sys_analyser={"enabled": False},
        sys_progress={"enabled": False},
        sys_risk={"enabled": False},
        sys_accounts={"enabled": True, "validate_stock_position": False},
    )
    # Never execute source to extract __config__: staged parameters and B3 policy own config.
    config = parse_config(overrides, source_code="")
    allowed = {"sys_accounts", "sys_transaction_cost", "sys_scheduler", "investorch_live"}
    for name, value in vars(config.mod).items():
        value.enabled = name in allowed
    config.base.end_date = None  # LIVE has no fabricated future end date.
    return config


def run_live(artifacts, control, on_status, *, market=None, clock=None):
    """Run a validated staged artifact; injection is limited to external market/time seams."""
    clock = clock or WallClock()
    market = market or XtDataAdapter()
    env, handler, subscription = None, None, None
    stack_depth = len(ExecutionContext.stack.stack)
    code, failure = EXIT_CODE.EXIT_SUCCESS, None
    try:
        config = _config(artifacts, clock)
        env = Environment(config, False)
        set_loggers(config)
        handler = ModHandler()
        handler.set_env(env)
        handler.start_up()
        try:
            native = BaseDataSource(config.base)
            board = LivePriceBoard(market)
            proxy = LiveDataProxy(native, board, market)
            calendar = [stamp.date() for stamp in proxy.get_trading_calendar()]
            proxy.available_data_range("1d")
        except Exception as exc:
            raise RuntimeFailure("DATA_BUNDLE_NOT_READY", "Standard native bundle/reference preflight failed.") from exc
        env.set_data_source(native)
        env.set_price_board(board)
        env.set_data_proxy(proxy)
        env.set_broker(TradingUnavailableBroker(env))
        _, positions = native_portfolio_inputs(RQAlphaLiveBootstrapSnapshot.from_wire(artifacts.bootstrap))
        holdings = set(dict(positions))
        proxy.validate_instruments(holdings)
        market.connect()
        market.start_liveness()
        subscription = SubscriptionManager(market, holdings)

        def desired():
            return set(env.get_universe()) | holdings

        def update_subscription(event=None):
            symbols = desired()
            proxy.validate_instruments(symbols)
            subscription.update(env.get_universe())
            for symbol in symbols:
                market.instrument_detail(symbol)
                market.trading_periods(symbol)

        def periods():
            # Calendar/session preflight also works for a strategy with no desired quotes.
            symbols = sorted(desired()) or ["600000.XSHG"]
            return tuple(period for symbol in symbols for period in market.trading_periods(symbol))

        events = DailyEventSource(
            calendar,
            periods,
            clock,
            control,
            on_status,
            proxy.require_fresh_history,
            lambda day, final: proxy.prepare_bars(desired(), day, final),
            market.check_health,
        )
        first = events.first_session()
        config.base.start_date = first
        config.base.trading_calendar = proxy.get_trading_calendar()
        initial = datetime.combine(first, time.min)
        env.update_time(initial, initial)
        env.set_event_source(events)
        env.set_portfolio(
            Portfolio(config.base.accounts, config.base.init_positions, config.mod.sys_accounts.financing_rate, env)
        )
        env.event_bus.add_listener(EVENT.POST_UNIVERSE_CHANGED, update_subscription)
        env.event_bus.publish_event(Event(EVENT.POST_SYSTEM_INIT))
        scope = create_base_scope()
        scope.update({"g": env.global_vars})
        scope.update(get_strategy_apis())
        loader = SourceCodeStrategyLoader(artifacts.source.decode("utf-8"))
        env.set_strategy_loader(loader)
        ExecutionContext(EXECUTION_PHASE.GLOBAL)._push()
        scope = loader.load(scope)
        if callable(scope.get("handle_tick")) or callable(scope.get("open_auction")):
            raise RuntimeFailure("UNSUPPORTED_FREQUENCY", "B3 does not support tick or open-auction callbacks.")
        context = StrategyContext()
        for name, value in config.extra.context_vars.items():
            setattr(context, name, value)
        strategy = Strategy(env.event_bus, scope, context)
        env.user_strategy = strategy
        env.event_bus.publish_event(Event(EVENT.BEFORE_STRATEGY_RUN))
        strategy.init()
        update_subscription()
        market.check_health()
        # Init may take time: crossing the first boundary before READY is still not a full session.
        if events.first_session() != first:
            raise RuntimeFailure("SESSION_ALREADY_STARTED", "Session changed during initialization.", retryable=True)
        if control.stopped.is_set():
            return
        on_status("READY", market_data="CONNECTED")
        on_status("RUNNING", market_data="CONNECTED")
        LiveExecutor(env).run(BarMap(proxy, "1d"))
        env.event_bus.publish_event(Event(EVENT.POST_STRATEGY_RUN))
    except BaseException as exc:
        code, failure = EXIT_CODE.EXIT_INTERNAL_ERROR, exc
        original = exc
        while isinstance(original, CustomException) and original.error.exc_val is not None:
            original = original.error.exc_val
        if isinstance(original, MarketDataError):
            raise RuntimeFailure(original.code, str(original), retryable=original.transient) from exc
        if isinstance(original, RuntimeFailure):
            raise original from exc
        raise
    finally:
        cleanup_error = None
        for cleanup in ([subscription.close] if subscription is not None else []) + [market.close]:
            try:
                cleanup()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        try:
            if handler is not None:
                handler.tear_down(code, failure)
        finally:
            del ExecutionContext.stack.stack[stack_depth:]
            if env is not None:
                cleanup_resources(env)
        if failure is None and cleanup_error is not None:
            raise cleanup_error
