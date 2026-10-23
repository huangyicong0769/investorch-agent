# Worked example: compare two momentum periods

This is a teaching transcript, not a record of a completed backtest. Every return and metric below is illustrative; actual IDs, paths, coverage and results come from the tools. Each named call is a separate Agent function-tool invocation, not a shell API or a batch endpoint. Calls use complete arguments, including optional values.

## Request and inputs

User: “Compare the supplied 20- and 60-trading-day momentum strategies during 2025, with CNY 100,000 initial cash and the current trading costs. Keep the fixed two-stock universe and monthly rebalancing. Give me traceable results; do not download or repair data.”

The two complete input files are distributed with this Skill as `assets/momentum20.py` and `assets/momentum60.py`. They rank `600000.XSHG` and `000001.XSHE` using completed bars, target a positive-momentum winner at 95% or target cash otherwise, and rebalance once per month. They need 21 and 61 previous bars respectively and explicitly use `skip_suspended=False` so horizons follow exchange trading days. At every monthly rebalance they query each stock's actual quantity and retry unwanted holdings; blocked exits can leave both stocks held until a later rebalance. This is a monthly one-winner target, not a guaranteed single holding. This fixed-universe teaching comparison is not a point-in-time index-constituent study. Costs, order rounding, T+1 and matching remain runner policy.

## Load guidance and inspect the exact inputs

Call `load_skill`:

```json
{"name":"rqalpha-strategy"}
```

Representative return: `{"name":"rqalpha-strategy","version":"1.0.5","content":"<the actual SKILL.md body>","resources":["assets/momentum20.py","assets/momentum60.py","references/examples.md","references/runtime.md"]}`. The content placeholder abbreviates this teaching return. Derive the installed prefix `skills/rqalpha-strategy/` from the returned name and use resources present in its inventory.

Call `explore` to read the required runtime reference, this example, and both complete source inputs, separately:

```json
{"operation":"read","path":"skills/rqalpha-strategy/references/runtime.md","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"skills/rqalpha-strategy/references/examples.md","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"skills/rqalpha-strategy/assets/momentum20.py","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"skills/rqalpha-strategy/assets/momentum60.py","query":"","start_line":0,"end_line":0}
```

Each text return includes `path`, line bounds and `content`. Review the actual sources: the only parameter difference should be 21 versus 61 requested bars and their length checks. Stop if supplied logic differs from the requested study.

Call `write_todos`:

```json
{"todos":[{"content":"Verify input strategies, coverage and effective costs","status":"in_progress"},{"content":"Run and verify the 20-day representative backtest","status":"pending"},{"content":"Run the 60-day comparison and verify artifacts","status":"pending"}]}
```

Return: `[>] Verify input strategies, coverage and effective costs` followed by the two pending rows. These are stages of one replaceable list.

## Establish effective policy and coverage

Call `get_config` for the mode and relevant policy leaf keys (each block is a separate call):

```json
{"key":"backtest.use_cnequity"}
```
```json
{"key":"backtest.slippage"}
```
```json
{"key":"backtest.slippage_model"}
```
```json
{"key":"backtest.matching_type"}
```
```json
{"key":"backtest.stock_t1"}
```
```json
{"key":"backtest.stock_min_commission"}
```
```json
{"key":"backtest.stock_commission_multiplier"}
```
```json
{"key":"backtest.tax_multiplier"}
```
```json
{"key":"backtest.pit_tax"}
```

Representative corresponding returns are `{"key":"backtest.use_cnequity","value":false}`, then values `0.0005`, `"PriceRatioSlippage"`, `"current_bar"`, `true`, `5.0`, `1.0`, `1.0`, `true`, each in the same `{"key":...,"value":...}` shape. Use actual values; never turn these teaching defaults into configuration changes. The complete defaults are also available with `get_config({"key":null})`. Both calls below use `parameters:null` to inherit them; verify the returned effective `runtime` and the same values in each saved request before comparing. Null inherits each invocation's snapshot, so confirm policy equality across the pair.

Because the returned mode is native, call `inspect_rqalpha_data` for both traded stocks and the benchmark:

```json
{"symbols":["600000.XSHG","000001.XSHE","000300.XSHG"]}
```

Representative return (selected fields):

```json
{"source":"rqalpha_bundle","available":true,"symbols":[{"order_book_id":"600000.XSHG","exists":true,"type":"CS","bar_start":"1999-11-10","bar_end":"2026-08-31"},{"order_book_id":"000001.XSHE","exists":true,"type":"CS","bar_start":"1991-04-03","bar_end":"2026-08-31"},{"order_book_id":"000300.XSHG","exists":true,"type":"INDX","bar_start":"2005-01-04","bar_end":"2026-08-31"}]}
```

These per-symbol ranges cover 2025 and at least 61 earlier trading bars. They are metadata evidence, not a guarantee that every requested history bar is usable: strategy length checks and the real run remain authoritative. If the mode were overlay, this inspection tool would be absent; follow the runtime reference, query exposed research coverage if available and rely on actual overlay errors, without guessing an inspection tool. If membership inputs were needed, first use the exposed MCP tools' actual schemas and save dated membership evidence separately; this fixed-universe request needs none.

## Prepare Workspace inputs and run the representative case

The source review above established these are already ordinary RQAlpha strategies. Copy the supplied files into a new research directory, through normal approval; this supports file preparation and does not implement an engine. Call `exec_command`:

```json
{"command":"mkdir research/momentum-example && cp skills/rqalpha-strategy/assets/momentum20.py research/momentum-example/momentum20.py && cp skills/rqalpha-strategy/assets/momentum60.py research/momentum-example/momentum60.py","background":false,"timeout_seconds":30}
```

Precondition: `research/` exists and `research/momentum-example/` does not; establish this with `explore({"operation":"list","path":".","query":"","start_line":0,"end_line":0})` and `explore({"operation":"list","path":"research","query":"","start_line":0,"end_line":0})`. If `research/` is absent, use `mkdir -p research` as a separate approved `exec_command` with `background=false, timeout_seconds=30`; if the study directory exists, choose an unused path based on its listing. Carry the selected path into both later calls. Successful copy return: `{"stdout":"","stderr":"","exit_code":0}`. When source edits are needed, use `edit` with explicit `operation`, complete content and `old_text`; review modified source before running.

Call `write_todos` with the full updated list:

```json
{"todos":[{"content":"Verify input strategies, coverage and effective costs","status":"completed"},{"content":"Run and verify the 20-day representative backtest","status":"in_progress"},{"content":"Run the 60-day comparison and verify artifacts","status":"pending"}]}
```

The returned formatted list confirms stage transition. Call `run_backtest` through normal approval:

```json
{"strategy_path":"research/momentum-example/momentum20.py","start_date":"2025-01-01","end_date":"2025-12-31","initial_cash":100000.0,"benchmark":"000300.XSHG","parameters":null}
```

Representative return (selected summary fields and complete artifact map for this stock run):

```json
{"run_id":"11111111111111111111111111111111","engine":"rqalpha","engine_version":"6.4.1","summary":{"strategy_file":"research/momentum-example/momentum20.py","total_returns":0.08,"annualized_returns":0.08,"max_drawdown":0.12},"artifacts":{"result_dir":"backtests/11111111111111111111111111111111","request":"backtests/11111111111111111111111111111111/request.json","strategy":"backtests/11111111111111111111111111111111/strategy.py","summary":"backtests/11111111111111111111111111111111/summary.json","portfolio":"backtests/11111111111111111111111111111111/portfolio.csv","trades":"backtests/11111111111111111111111111111111/trades.csv","stock_account":"backtests/11111111111111111111111111111111/stock_account.csv","stock_positions":"backtests/11111111111111111111111111111111/stock_positions.csv","benchmark_portfolio":"backtests/11111111111111111111111111111111/benchmark_portfolio.csv"},"runtime":{"stock_t1":true,"dividend_reinvestment":false,"dividend_tax_enabled":true,"capital_gain_tax_rate":0.0,"validate_price":true,"matching_type":"current_bar","price_limit":true,"volume_limit":true,"inactive_limit":true,"slippage_model":"PriceRatioSlippage","slippage":0.0005,"stock_min_commission":5.0,"stock_commission_multiplier":1.0,"tax_multiplier":1.0,"pit_tax":true}}
```

Call `explore` on the returned request and saved summary:

```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/request.json","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/summary.json","query":"","start_line":0,"end_line":0}
```

The request content must identify the actual strategy path, `strategy_sha256`, interval, capital, benchmark and data source. Its `runtime` must equal the returned effective `runtime`; the saved summary must agree with the result. These are provenance checks. Before executing the second case, inspect the saved strategy and actual behavior as well:

```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/strategy.py","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/stock_positions.csv","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/trades.csv","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/stock_account.csv","query":"","start_line":0,"end_line":0}
```

Use `exec_command` for statistics over the complete returned CSVs when an excerpt is insufficient. Count positive-quantity holdings by date and inspect their weights, cash and transitions around monthly rebalances. Compare actual entries and exits with the snapshot's targets and 21-bar signal definition. When a losing stock remains held, check whether it receives another exit attempt at the next scheduled rebalance; use available order diagnostics to distinguish a blocked exit from missing strategy logic. Trades show fills, so their absence alone proves neither that an order was attempted nor why it failed. Record unavailable rejection evidence and investigate a material unexplained residual before scaling. The observed behavior must match this monthly target/retry definition, including disclosed execution deviations; source hashes and readable tables alone do not pass that check.

On a tool error, preserve the call and returned safe diagnostic. For example, an exception pointing to `bar_dict[stock].is_suspended` calls for checking that expression against the runtime API and using `is_suspended(stock)` if needed. Preserve the valid position queries. Rerun the corrected representative case before expanding. Leave execution uncompleted when a diagnostic or capability gap prevents verification.

## Run the second parameter and verify the pair

Call `write_todos`:

```json
{"todos":[{"content":"Verify input strategies, coverage and effective costs","status":"completed"},{"content":"Run and verify the 20-day representative backtest","status":"completed"},{"content":"Run the 60-day comparison and verify artifacts","status":"in_progress"}]}
```

Call `run_backtest`, carrying the same interval, capital and benchmark:

```json
{"strategy_path":"research/momentum-example/momentum60.py","start_date":"2025-01-01","end_date":"2025-12-31","initial_cash":100000.0,"benchmark":"000300.XSHG","parameters":null}
```

Representative return has the same effective `runtime` as the first call, `run_id="22222222222222222222222222222222"`, `engine="rqalpha"`, `engine_version="6.4.1"`, `summary.strategy_file="research/momentum-example/momentum60.py"`, illustrative `total_returns=0.06`, `annualized_returns=0.06`, `max_drawdown=0.09`, and the same artifact keys under the newly returned result directory. Read the second request and summary:

```json
{"operation":"read","path":"backtests/22222222222222222222222222222222/request.json","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"backtests/22222222222222222222222222222222/summary.json","query":"","start_line":0,"end_line":0}
```

For this transcript's returned paths, call `exec_command` to verify source snapshots, all saved tables and policy equality. On real runs substitute the actual returned directory and table paths; do not assume any ID or optional table exists:

```json
{"command":"python3 - <<'PY'\nimport hashlib, json\nfrom pathlib import Path\nroots = [Path('backtests/11111111111111111111111111111111'), Path('backtests/22222222222222222222222222222222')]\nrequests = []\nfor root in roots:\n    request = json.loads((root / 'request.json').read_text())\n    summary = json.loads((root / 'summary.json').read_text())\n    source = (root / 'strategy.py').read_bytes()\n    assert hashlib.sha256(source).hexdigest() == request['strategy_sha256']\n    assert source == Path(request['strategy_path']).read_bytes()\n    assert summary['strategy_file'] == request['strategy_path']\n    for name in ['portfolio', 'trades', 'stock_account', 'stock_positions', 'benchmark_portfolio']:\n        assert (root / (name + '.csv')).is_file(), name\n    requests.append(request)\n    print(json.dumps({'run_id': request['run_id'], 'strategy': request['strategy_path'], 'return': summary['total_returns'], 'drawdown': summary['max_drawdown']}))\nfor key in ['engine', 'engine_version', 'data_source', 'start_date', 'end_date', 'initial_cash', 'benchmark', 'runtime']:\n    assert requests[0][key] == requests[1][key], key\nprint('source snapshots, artifacts and shared execution policy verified')\nPY","background":false,"timeout_seconds":30}
```

Return must have `exit_code=0`, the two actual metric rows, and the final verification message. A failed assertion is a failed verification, not permission to claim completion. This check verifies provenance and shared execution policy. Complete the actual-holdings and trading checks above for the second run too; table existence does not validate behavior. Shell statistics may analyze returned tables, but RQAlpha remains the source of matching, holdings and costs.

For a paired comparison, retain both observed CAGRs and label derived metrics consistently. With the illustrative 8% and 6% CAGRs, the 20-day minus 60-day CAGR difference is 2 percentage points; the geometric annual relative return is `1.08 / 1.06 - 1`, approximately 1.89%. Apply the same definition to every benchmark or control, keeping these two metrics in separate columns. Shared capital and policy support comparability; this pair alone does not isolate momentum, timing or cost contributions. Check the resulting prose against both actual rows before marking verification complete.

Call `write_todos` only after those checks succeed:

```json
{"todos":[{"content":"Verify input strategies, coverage and effective costs","status":"completed"},{"content":"Run and verify the 20-day representative backtest","status":"completed"},{"content":"Run the 60-day comparison and verify artifacts","status":"completed"}]}
```

## Example final response

“Both 2025 comparisons ran through RQAlpha with CNY 100,000, the same observed execution policy and the supplied two-stock universe. In these illustrative results, 20-day momentum returned 8% with 12% maximum drawdown; 60-day returned 6% with 9% drawdown. The CAGR difference was 2 percentage points and the geometric annual relative return was 1.89%. The higher return came with a larger drawdown. Strategy snapshots, source hashes and saved policies matched. Actual holdings and trades were checked against the monthly target and blocked-exit retry definition. Results: `backtests/11111111111111111111111111111111/` and `backtests/22222222222222222222222222222222/`. These results cover this fixed universe, interval and pair of parameters.”

In a real answer replace every teaching value/path with the observed result, link the useful Workspace artifacts and include observed execution deviations, unavailable rejection evidence, any unexecuted combination and data limitations. A failed or unfinished behavior check remains pending.

## Pass execution assumptions for one invocation

For a study that requests different execution assumptions, keep the same tool-call structure and supply a complete `parameters` object. For example:

```json
{"stock_t1":null,"dividend_reinvestment":null,"dividend_tax_enabled":null,"capital_gain_tax_rate":null,"validate_price":null,"matching_type":null,"price_limit":null,"volume_limit":null,"inactive_limit":null,"slippage_model":null,"slippage":0,"stock_min_commission":0,"stock_commission_multiplier":0.375,"tax_multiplier":null,"pit_tax":false}
```

This sets zero slippage and minimum commission, a stock commission rate of `0.0008 * 0.375 = 0.0003`, and a fixed rather than historical stamp-tax rate. `pit_tax=false` does not remove tax. Other fields inherit AppConfig defaults. Pass this object as the `parameters` argument; it changes only that invocation and needs no `update_config`. Check the returned `runtime` against the request artifact. A cost-sensitivity comparison varies the intended fields and holds the other effective parameters constant.
