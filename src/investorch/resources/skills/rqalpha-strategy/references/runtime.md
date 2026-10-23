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
| Execution policy | AppConfig defaults overlaid by `run_backtest.parameters` for this invocation |

Daily frequency and the stock account are capability contracts. `initial_cash` and `benchmark` remain separate tool arguments; `initial_cash=null` uses `backtest.default_initial_cash`. Supply the user's capital explicitly when given. The strategy owns trading logic; the runner resolves these execution controls:

| Parameter | Meaning and unit | Valid override |
| --- | --- | --- |
| `stock_t1` | Enforce the instrument's T+1 stock-selling restriction on shares bought that day. False removes this restriction. | `true` or `false` |
| `dividend_reinvestment` | Reinvest payable cash dividends in the same instrument; false leaves the payment as cash. | `true` or `false` |
| `dividend_tax_enabled` | Deduct the engine's holding-period dividend tax when shares are sold; false disables this dividend tax. | `true` or `false` |
| `capital_gain_tax_rate` | Decimal rate applied at month-end to the engine's positive monthly realized-gain tax basis after within-year loss offsets. Zero disables this tax. | Finite number `>= 0` |
| `validate_price` | Check submitted LIMIT-order prices against the upper and lower price limits before accepting the order. | `true` or `false` |
| `matching_type` | Use the current daily bar's closing price as the matching reference, before slippage. | `"current_bar"` |
| `price_limit` | Enforce matching restrictions on buys at the upper price limit and sells at the lower price limit, using the engine's tick-size tolerance. Separate from submission-time `validate_price`. | `true` or `false` |
| `volume_limit` | Cap cumulative matched shares per instrument and daily bar using the engine's `volume_percent` participation ratio, rounded to tradable quantities. False removes this cap. | `true` or `false` |
| `inactive_limit` | Cancel orders when the daily bar's volume is zero. False disables this check. | `true` or `false` |
| `slippage_model` | Adjust the matching reference by a proportional price change using `slippage`. | `"PriceRatioSlippage"` |
| `slippage` | Decimal price ratio `r`: buy reference times `(1+r)`, sell reference times `(1-r)`, then clamp to available upper/lower price limits. Zero removes the ratio adjustment. | Finite number `0 <= r < 1` |
| `stock_min_commission` | Minimum stock commission per order, in account currency. Independent of the commission multiplier. | Finite number `>= 0` |
| `stock_commission_multiplier` | Unitless multiplier of RQAlpha's base stock commission rate `0.0008`. Zero removes the proportional commission, but the minimum still applies. | Finite number `>= 0` |
| `tax_multiplier` | Unitless multiplier of the engine's sell-side stock stamp tax. Zero removes this transaction tax; dividend and capital-gain taxes are separate. | Finite number `>= 0` |
| `pit_tax` | True selects historical stock stamp-tax rates by trading date; false selects the engine's fixed rate and does not disable tax. | `true` or `false` |

`parameters=null`, an omitted object, or a null/omitted field inherits the corresponding `backtest.<field>` AppConfig default. Explicit `false` and `0` are overrides. Read defaults with `get_config` when needed, then pass study-specific assumptions in `parameters`. Strict tool schemas require every declared property in model-generated calls: use null for inherited fields. Values have strict types; strings and booleans are not numeric inputs. Resolve each invocation independently without changing AppConfig. A request to vary costs or execution assumptions for a study does not call `update_config`; a request to change future application defaults belongs to the configuration workflow.

Default values come from AppConfig: read `backtest.<field>` with `get_config` and verify the resolved `runtime`. The volume participation ratio itself is not exposed by `parameters`. Capital-gain loss offsets reset at the start of each year under the engine's calculation.

The tool returns all 15 effective fields in `runtime` and saves the same values in `request.json.runtime`. Use these resolved values to verify comparisons. `backtest.use_cnequity`, bundle/configuration paths, artifact paths and inspection limits remain application settings rather than per-run parameters.

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

`history_bars(stock, 21, "1d", "close", skip_suspended=False, include_now=False, adjust_type="pre")`

These 21 completed closes support a 20-exchange-trading-day return, `closes[-1] / closes[0] - 1`, ending at the previous session. For momentum over L exchange trading days, request L+1 bars with `skip_suspended=False` and check the returned length. The default `skip_suspended=True` instead counts non-suspended observations; label that different horizon explicitly when intended.

Historical warm-up uses data available before the configured backtest start. On the first simulation day, `history_bars` can already return the completed pre-start bars supplied by the configured source. Establish the first valid signal from actual coverage, returned dates and length, and verify the first observed rebalance in the run artifacts.

In the pinned daily runtime, `history_bars` called during `handle_bar` includes the current daily bar even with `include_now=False`; the flag alone does not establish prior-bar timing. Compute prior-day signals in `before_trading` and use them in `handle_bar`. If intentionally using the current close, state that information/execution assumption explicitly. RQAlpha does not prevent every logical look-ahead error.

Use `bar_dict[stock]` for the current daily `open`, `high`, `low`, `close`, `volume`, and `total_turnover`. A missing normal bar from the configured backtest source is an error; it is not a zero-volume observation. Do not infer minute paths, auction detail, or tick behavior from a daily bar.

Suspension status is a separate API: `is_suspended(stock)`, imported from `rqalpha.api`. A daily bar has no `is_suspended` field. Let the runner enforce trading restrictions even when the strategy also checks suspension status.

## Trading and portfolio APIs

Approved stock order subset:

- `order_shares(id, amount)`: trade a signed share count; A-share quantities are rounded to board lots.
- `order_value(id, cash_amount)`: trade a signed cash value.
- `order_percent(id, percent)`: trade a signed fraction of current portfolio value.
- `order_target_value(id, cash_amount)`: move one position toward a target value.
- `order_target_percent(id, percent)`: move one position toward a target portfolio weight.

Target-position APIs are usually clearer for rebalancing because they express the desired final allocation. For state, use `context.portfolio.cash`, `context.portfolio.total_value`, `get_position(order_book_id)`, and `get_positions()`. Avoid the deprecated `context.portfolio.positions` access path.

### Actual positions and rebalance targets

Keep desired targets separate from actual holdings. Each `get_positions()` entry exposes `order_book_id`, `quantity` and `market_value`; use positive quantities to discover held stocks, including stocks that left a changing universe. `get_position(stock).quantity` is suitable when checking every member of a fixed, complete universe, as in the supplied momentum assets.

On each scheduled rebalance, derive sell candidates from actual positions and submit exits for holdings absent from the desired target. Keep remaining positions eligible for later exit attempts until observed quantity reaches zero. For a changing universe, include actual holdings with current candidates in `update_universe` so dropped constituents remain visible. Assigning a selected-stock list records the signal, not the outcome of orders.

An order request can be rejected, partially filled or remain open; a returned order, when present, exposes `status`, `filled_quantity` and `message`. Reconcile with actual positions after matching, using `after_trading` for end-of-day observation. Set the retry schedule and the handling of blocked exits in the strategy definition. If a strict holdings limit is required, reserve capacity for retained positions or defer replacement buys; a target of K stocks alone does not guarantee K actual holdings. Board lots, T+1, suspension and price/volume limits can all cause deviations.

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
        skip_suspended=False,
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

## Acceptance and comparison

Before expanding a representative run, verify both:

1. **Provenance:** saved request, strategy snapshot/hash, parameters, interval, capital, benchmark and data source match the intended inputs; returned `runtime` equals `request.json.runtime` and the intended effective execution policy.
2. **Strategy behavior:** read the snapshot and inspect actual `stock_positions`, `trades` and portfolio/account tables. Compare observed holdings counts, weights, cash, turnover and exit attempts with the strategy's selection, rebalance and blocked-exit rules. For changing universes, check residual holdings against membership effective on each observed date, including after removals. Investigate excess counts, persistent unwanted positions and rejected/unfilled orders before scaling. Inspect available order diagnostics; exported trades record fills, not every failed order. If rejection evidence is unavailable, state that limit and use a bounded diagnostic run when necessary. A passing hash/table-existence check establishes provenance, not strategy correctness.

Accept a run only when deviations have an evidenced explanation consistent with the agreed strategy definition. A failed exit that is retried under that definition differs from a forgotten holding. Repair a logic error and rerun affected results before using them in a comparison. Apply relevant checks to the full matrix as well as the representative case.

Identical portfolio series can result from different strategies selecting the same holdings in the observed window. Check snapshots, signals and fills before interpreting equality; matching outputs alone do not prove duplicated execution or corrupted artifacts. Missing supporting artifacts leave that check unverified, rather than establishing an integrity failure.

Use the same initial cash, period, execution constraints and cost policy for paired controls unless the research question explicitly varies them. If capital differs, board-lot rounding, minimum fees and volume constraints can affect the comparison; report the difference and test it before attributing the return gap to stock selection. State actual currency amounts and units from saved requests.

Define each reported metric from its actual observations, timestamps and denominator. Distinguish entry allocation, daily closing equity exposure and mean exposure. Mean daily equity exposure is the arithmetic mean of each observed day's stock market value divided by that day's total portfolio value; state the observation window. Compute this series from saved account/position tables rather than relabeling the entry allocation as its average.

For a window with N trading observations and an annualization convention of D trading days, compound annualized return is `(final_NAV / initial_NAV) ** (D / N) - 1`. State D and N; a short-window annualized return is a mechanical compound projection, not linear scaling or evidence about a future year.

When deriving daily returns from N saved end-of-day NAV observations, prepend the request's initial NAV: the first return is `NAV[0] / initial_NAV - 1`, followed by each day's change from the preceding close. This gives N returns, including first-day fees or market moves. Apply the same boundary to the benchmark; do not silently discard its first day with `pct_change().dropna()`. For volatility and Sharpe, state the annualization factor, standard-deviation convention and risk-free rate, and reconcile any difference from engine metrics before presenting them as equivalent.

A discrepancy from an engine ratio establishes a difference in results; its cause requires evidence about the engine's formula and inputs. If those are unavailable, report the unresolved definition rather than infer an engine defect or a particular risk-free rate from numerical guesses.

Use consistent comparison metrics for every baseline. For strategy CAGR g_s and baseline CAGR g_b over the same interval:

- CAGR difference is `g_s - g_b`, reported in percentage points.
- Geometric annual relative return is `(1 + g_s) / (1 + g_b) - 1`, reported as a percent. RQAlpha's `excess_annual_returns` uses this geometric convention.

Keep these columns separate. Differences between geometric relative returns are not additive contribution components; paired return gaps alone do not identify causal momentum, cost or signal effects. Attribution requires controls that isolate the claimed factor. Check each narrative claim against all relevant table rows, and limit trend/optimality claims to tested parameters and samples. Fixed snapshot membership supports a fixed-universe study; historical index membership requires dated point-in-time inputs.
