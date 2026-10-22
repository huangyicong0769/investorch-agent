# InvestOrch Agent RQAlpha Strategy Guide

## Purpose and source of truth

- The packaged engine is RQAlpha 6.4.1. This guide defines the subset supported by the current InvestOrch Agent backtest runtime; it is not the complete upstream API manual. The actual tool result reports `engine_version`.
- Project restrictions win when broader RQAlpha features differ from this guide. Do not guess unsupported behavior.
- Strategies are ordinary RQAlpha Python files. InvestOrch Agent supplies the runtime configuration externally; strategy code must not configure or mutate it.
- API names and signatures below correspond to the project's pinned `rqalpha==6.4.1` installation.

## Runtime contract

| Setting | Value |
| --- | --- |
| Engine / run type | RQAlpha 6.4.1 / backtest |
| Frequency / account | `1d` / stock |
| Execution policy | Effective `backtest.*` values in the current `AppConfig` |

Daily frequency and the stock account are capability contracts. T+1, matching, price/volume/inactive limits, slippage, commission, dividend handling, and tax are application policy. Read the relevant effective leaf keys with `get_config`: `backtest.stock_t1`, `backtest.matching_type`, `backtest.validate_price`, `backtest.price_limit`, `backtest.volume_limit`, `backtest.inactive_limit`, `backtest.slippage_model`, `backtest.slippage`, `backtest.stock_min_commission`, `backtest.stock_commission_multiplier`, `backtest.tax_multiplier`, `backtest.pit_tax`, `backtest.dividend_reinvestment`, `backtest.dividend_tax_enabled`, and `backtest.capital_gain_tax_rate`. `run_backtest(initial_cash=null)` uses `backtest.default_initial_cash`; supply the user's capital explicitly when given. These settings belong to the runner; a strategy should contain trading logic, not runtime configuration.

## Market-data ownership

The process configuration snapshot selects one of two backtest data paths.

With `backtest.use_cnequity=false`, strategy API calls flow through `RQAlpha DataProxy -> BaseDataSource -> RQAlpha bundle`, and the built-in CNEquity MCP server is not started. Other configured MCP research servers can still be available. Use `inspect_rqalpha_data` as the authority for native bundle instrument metadata and observed daily-bar ranges. Do not infer RQAlpha coverage from independently configured research sources.

With `backtest.use_cnequity=true`, strategy API calls flow through `RQAlpha DataProxy -> CNEquityOverlayDataSource`. For Shanghai and Shenzhen stocks, CNEquity supplies daily OHLCV, turnover, and adjustment factors, while native RQAlpha data retains instruments, calendars, limits, corporate actions, suspension evidence, and fallback market semantics. The RQAlpha inspection Tool is not exposed in this mode.

Never invoke the CNEquity CLI, MCP, Python ingestion lifecycle, or RQAlpha bundle updater inside a strategy. CNEquity MCP remains an independent research/query plane in both modes; its availability does not identify the configured backtest source.

## Instrument identifiers

Use canonical RQAlpha order-book IDs in strategy code:

- Shanghai: `600519.XSHG`
- Shenzhen: `000001.XSHE`

`600519.SH` is a CNEquity-style symbol; `600519.XSHG` is the strategy ID. Do not invent another symbol abstraction. The current CNEquity overlay is approved for Shanghai/Shenzhen stocks; do not assume an ETF or another instrument type is supported by that overlay without a concrete runtime acceptance.

## Strategy lifecycle

- `init(context)`: initialize durable in-run state and call `update_universe`. `history_bars` is not available here.
- `before_trading(context)`: perform daily preparation before the session. In a daily backtest, daily history observed here ends at the previous trading day.
- `handle_bar(context, bar_dict)`: read the current daily bars and place normal orders. `bar_dict[order_book_id]` is a daily bar, not an intraday snapshot.
- `after_trading(context)`: record or update end-of-day strategy state; do not place ordinary orders here.

All callbacks except `handle_bar` receive only `context` in the approved form.

## Historical data and current bars

Pinned signature:

`history_bars(order_book_id, bar_count, frequency, fields=None, skip_suspended=True, include_now=False, adjust_type="pre")`

Use `frequency="1d"`. Common stock fields are `datetime`, `open`, `high`, `low`, `close`, `volume`, and `total_turnover`. Prefer `adjust_type="pre"` unless the research question explicitly requires raw (`"none"`) or post-adjusted (`"post"`) prices. `skip_suspended=True` omits suspended days from the returned series.

Build signals in `before_trading` from completed historical bars by default:

`history_bars(stock, 20, "1d", "close", include_now=False, adjust_type="pre")`

In the pinned daily runtime, `history_bars` called during `handle_bar` includes the current daily bar even with `include_now=False`; the flag alone does not establish prior-bar timing. Compute prior-day signals in `before_trading` and use them in `handle_bar`. If intentionally using the current close, state that information/execution assumption explicitly. RQAlpha does not prevent every logical look-ahead error. Also choose a suspension convention: the default `skip_suspended=True` counts non-suspended observations; use `skip_suspended=False` when the horizon should follow exchange trading days.

Use `bar_dict[stock]` for the current daily `open`, `high`, `low`, `close`, `volume`, and `total_turnover`. A missing normal bar from the configured backtest source is an error; it is not a zero-volume observation. Do not infer minute paths, auction detail, or tick behavior from a daily bar.

## Trading and portfolio APIs

Approved stock order subset:

- `order_shares(id, amount)`: trade a signed share count; A-share quantities are rounded to board lots.
- `order_value(id, cash_amount)`: trade a signed cash value.
- `order_percent(id, percent)`: trade a signed fraction of current portfolio value.
- `order_target_value(id, cash_amount)`: move one position toward a target value.
- `order_target_percent(id, percent)`: move one position toward a target portfolio weight.

Target-position APIs are usually clearer for rebalancing because they express the desired final allocation. For state, use `context.portfolio.cash`, `context.portfolio.total_value`, `get_position(order_book_id)`, and `get_positions()`. Avoid the deprecated `context.portfolio.positions` access path.

The daily runtime calls `handle_bar` every trading day, so no scheduler is needed for daily work. For weekly or monthly rebalancing, use a small date/state guard unless a separately tested requirement justifies scheduler helpers; do not import minute-time scheduling semantics.

## Unsupported and forbidden patterns

Do not use:

- minute/tick assumptions, minute APIs, or intraday-only market-data semantics;
- `open_auction` callbacks, auction bars, or auction-volume APIs; the CNEquity overlay additionally rejects them at runtime;
- future data or unacknowledged current-bar-close timing assumptions;
- automatic CNEquity init/daily/retry/backfill commands or automatic RQAlpha bundle downloads;
- MCP calls inside strategy execution;
- network calls, package installation, shell/subprocess execution, secret access, unrelated host filesystem mutation, or self-modifying source.

A strategy file should primarily describe trading logic. Approval authorizes execution but is not a Python sandbox.

## Canonical examples

The following are complete daily strategies. They use locally accepted canonical IDs; choose instruments whose configured backtest source contains the requested period.

### 1. Buy and hold (minimal skeleton)

```python
from rqalpha.api import get_position, order_target_percent, update_universe


def init(context):
    context.stock = "002808.XSHE"
    update_universe([context.stock])


def handle_bar(context, bar_dict):
    if get_position(context.stock).quantity == 0:
        order_target_percent(context.stock, 1.0)
```

### 2. Moving-average crossover

```python
from rqalpha.api import get_position, history_bars, order_target_percent, update_universe


def init(context):
    context.stock = "002808.XSHE"
    context.bullish = False
    update_universe([context.stock])


def before_trading(context):
    closes = history_bars(
        context.stock,
        60,
        "1d",
        "close",
        include_now=False,
        adjust_type="pre",
    )
    context.bullish = len(closes) == 60 and closes[-20:].mean() > closes.mean()


def handle_bar(context, bar_dict):
    position = get_position(context.stock)
    if context.bullish and position.quantity == 0:
        order_target_percent(context.stock, 1.0)
    elif not context.bullish and position.quantity > 0:
        order_target_percent(context.stock, 0.0)
```

### 3. Monthly periodic rebalance

```python
from rqalpha.api import order_target_percent, update_universe


def init(context):
    context.stock = "600599.XSHG"
    context.last_rebalance = None
    update_universe([context.stock])


def handle_bar(context, bar_dict):
    month = (context.now.year, context.now.month)
    if month == context.last_rebalance:
        return
    order_target_percent(context.stock, 0.5)
    context.last_rebalance = month
```

### 4. Multi-instrument equal weight

```python
from rqalpha.api import order_target_percent, update_universe


def init(context):
    context.stocks = ["002808.XSHE", "600599.XSHG"]
    context.last_rebalance = None
    update_universe(context.stocks)


def handle_bar(context, bar_dict):
    month = (context.now.year, context.now.month)
    if month == context.last_rebalance:
        return
    weight = 1.0 / len(context.stocks)
    for stock in context.stocks:
        order_target_percent(stock, weight)
    context.last_rebalance = month
```

## Common mistakes

| Mistake | Preferred correction |
| --- | --- |
| CNEquity symbol such as `600519.SH` in strategy code | Use `600519.XSHG`. |
| Daily `handle_bar` history accidentally includes the current bar | Compute prior-day signals in `before_trading`; `include_now=False` alone does not exclude it. |
| Signal/execution timing is implicit | State the timing model and use prior completed bars by default. |
| `open_auction` or auction-volume API | Explain that truthful auction data is unsupported; do not run it. |
| Minute API in the daily runtime | Redesign the strategy around daily observations. |
| Runtime configuration inside the strategy | Leave configuration to InvestOrch Agent's runner. |
| Strategy repairs/downloads data | Surface the coverage/bundle error to the human. |
| Filesystem, network, or subprocess side effects | Keep the file focused on deterministic trading logic. |
| Missing bar treated as zero volume | Let the configured data source and runner validation fail closed. |

## InvestOrch Agent workflow

1. Read relevant user/project Memory when the task depends on prior context.
2. Load `rqalpha-strategy` and this runtime reference.
3. Use `get_config` to determine the active data and backtest execution policy when it matters.
4. When `inspect_rqalpha_data` is available and a new instrument, period, or uncertain coverage matters, inspect the intended canonical IDs before choosing the backtest window.
5. Design the strategy.
6. Write and review a Workspace `.py` strategy.
7. Check timing/look-ahead and unsupported APIs.
8. Call `run_backtest` after user approval.
9. Use the compact summary first.
10. Inspect saved CSV/JSON artifacts only when deeper analysis is needed.
11. Revise and rerun when appropriate.
