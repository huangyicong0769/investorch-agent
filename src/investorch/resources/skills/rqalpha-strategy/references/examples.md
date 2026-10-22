# Worked example: compare two momentum periods

This is a teaching transcript, not a record of a completed backtest. Every return and metric below is illustrative; actual IDs, paths, coverage and results come from the tools. Each named call is a separate Agent function-tool invocation, not a shell API or a batch endpoint. Calls use complete arguments, including optional values.

## Request and inputs

User: “Compare the supplied 20- and 60-trading-day momentum strategies during 2025, with CNY 100,000 initial cash and the current trading costs. Keep the fixed two-stock universe and monthly rebalancing. Give me traceable results; do not download or repair data.”

The two complete input files are distributed with this Skill as `assets/momentum20.py` and `assets/momentum60.py`. They rank `600000.XSHG` and `000001.XSHE` using completed bars, buy a positive-momentum winner at a 95% target weight, otherwise hold cash, and rebalance once per month. They need 21 and 61 previous bars respectively and explicitly use `skip_suspended=False` so horizons follow exchange trading days. This fixed-universe teaching comparison is not a point-in-time index-constituent study. Costs, order rounding, T+1 and matching remain runner policy.

## Load guidance and inspect the exact inputs

Call `load_skill`:

```json
{"name":"rqalpha-strategy"}
```

Representative return: `{"name":"rqalpha-strategy","version":"1.0.1","content":"<the actual SKILL.md body>","resources":["assets/momentum20.py","assets/momentum60.py","references/examples.md","references/runtime.md"]}`. Here the content placeholder only abbreviates the teaching return; it is not a missing executable input. Derive the installed prefix `skills/rqalpha-strategy/` from the returned name and use resources present in its inventory.

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

Representative corresponding returns are `{"key":"backtest.use_cnequity","value":false}`, then values `0.0005`, `"PriceRatioSlippage"`, `"current_bar"`, `true`, `5.0`, `1.0`, `1.0`, `true`, each in the same `{"key":...,"value":...}` shape. Use actual values; never turn these teaching defaults into configuration changes. The complete effective policy is also available with `get_config({"key":null})`; remaining price/volume/dividend/tax controls are recorded in each saved request for comparison.

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
{"strategy_path":"research/momentum-example/momentum20.py","start_date":"2025-01-01","end_date":"2025-12-31","initial_cash":100000.0,"benchmark":"000300.XSHG"}
```

Representative return (selected summary fields and complete artifact map for this stock run):

```json
{"run_id":"11111111111111111111111111111111","engine":"rqalpha","engine_version":"6.4.1","summary":{"strategy_file":"research/momentum-example/momentum20.py","total_returns":0.08,"annualized_returns":0.08,"max_drawdown":0.12},"artifacts":{"result_dir":"backtests/11111111111111111111111111111111","request":"backtests/11111111111111111111111111111111/request.json","strategy":"backtests/11111111111111111111111111111111/strategy.py","summary":"backtests/11111111111111111111111111111111/summary.json","portfolio":"backtests/11111111111111111111111111111111/portfolio.csv","trades":"backtests/11111111111111111111111111111111/trades.csv","stock_account":"backtests/11111111111111111111111111111111/stock_account.csv","stock_positions":"backtests/11111111111111111111111111111111/stock_positions.csv","benchmark_portfolio":"backtests/11111111111111111111111111111111/benchmark_portfolio.csv"}}
```

Call `explore` on the returned request and saved summary:

```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/request.json","query":"","start_line":0,"end_line":0}
```
```json
{"operation":"read","path":"backtests/11111111111111111111111111111111/summary.json","query":"","start_line":0,"end_line":0}
```

The request content must identify the actual strategy path, `strategy_sha256`, interval, capital, benchmark, data source and runtime costs; the saved summary must agree with the result. Use these observed fields to decide whether the representative run is valid before executing the second case. On a tool error, preserve the call and exact error and leave execution uncompleted; do not replace the runner.

## Run the second parameter and verify the pair

Call `write_todos`:

```json
{"todos":[{"content":"Verify input strategies, coverage and effective costs","status":"completed"},{"content":"Run and verify the 20-day representative backtest","status":"completed"},{"content":"Run the 60-day comparison and verify artifacts","status":"in_progress"}]}
```

Call `run_backtest`, carrying the same interval, capital and benchmark:

```json
{"strategy_path":"research/momentum-example/momentum60.py","start_date":"2025-01-01","end_date":"2025-12-31","initial_cash":100000.0,"benchmark":"000300.XSHG"}
```

Representative return has `run_id="22222222222222222222222222222222"`, `engine="rqalpha"`, `engine_version="6.4.1"`, `summary.strategy_file="research/momentum-example/momentum60.py"`, illustrative `total_returns=0.06`, `annualized_returns=0.06`, `max_drawdown=0.09`, and the same artifact keys under the newly returned result directory. Read the second request and summary:

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

Return must have `exit_code=0`, the two actual metric rows, and the final verification message. A failed assertion is a failed verification, not permission to claim completion. This check verifies provenance and comparability; it does not independently validate trading logic or future profitability. Shell statistics may analyze returned tables, but RQAlpha remains the source of matching, holdings and costs.

Call `write_todos` only after those checks succeed:

```json
{"todos":[{"content":"Verify input strategies, coverage and effective costs","status":"completed"},{"content":"Run and verify the 20-day representative backtest","status":"completed"},{"content":"Run the 60-day comparison and verify artifacts","status":"completed"}]}
```

## Example final response

“Both 2025 comparisons ran through RQAlpha with CNY 100,000, the same observed execution policy and the supplied two-stock universe. In these illustrative results, 20-day momentum returned 8% with 12% maximum drawdown; 60-day returned 6% with 9% drawdown. The higher return came with a larger drawdown. Strategy snapshots, source hashes and saved policies matched. Results: `backtests/11111111111111111111111111111111/` and `backtests/22222222222222222222222222222222/`. This is one historical fixed-universe comparison, not evidence that either period is generally optimal or a verified CSI300 constituent strategy.”

In a real answer replace every teaching value/path with the observed result, link the useful Workspace artifacts and state any unexecuted combination or data limitation.
