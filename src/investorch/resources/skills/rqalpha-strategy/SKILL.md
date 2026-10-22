---
name: rqalpha-strategy
description: Research and compare trading strategy performance, momentum periods, parameter grids or trading-cost sensitivity; write, review, debug and run daily stock backtests using the available RQAlpha toolchain and interpret its artifacts.
version: 1.0.1
---

# RQAlpha strategy workflow

1. Read `references/runtime.md` before specialized work. Before the first comparison or parameter grid, also read `references/examples.md` for a complete two-parameter workflow, including the supplied strategy fixtures and result verification.
2. Establish the universe, period, signal timing and user/project decisions. Query concrete effective keys with `get_config`, beginning with `backtest.use_cnequity`, `backtest.slippage`, `backtest.slippage_model`, `backtest.matching_type`, `backtest.stock_t1`, `backtest.stock_min_commission`, `backtest.stock_commission_multiplier`, `backtest.tax_multiplier` and `backtest.pit_tax`. Query other relevant policies in the runtime reference. `get_config(key="backtest")` is not a valid field lookup; use dotted leaf keys or `get_config(key=null)` for the full effective configuration. Preserve current costs unless the user requests a change.
3. In native mode, call `inspect_rqalpha_data` for the actual instruments, benchmark and necessary signal warm-up. Confirm each observed bar range rather than extending one index's coverage to its constituents. In overlay mode that tool is not exposed: follow the overlay contract in the runtime reference and report actual coverage errors returned by `run_backtest`. Neither mode requires shell access to the configured bundle.
4. Use available MCP query tools for research inputs such as historical index membership. Inspect the exposed MCP schema and carry returned symbols and effective dates into a Workspace input artifact. These queries are a research plane, not proof of backtest price coverage. If point-in-time membership is unavailable, report the missing input; a fixed current universe changes the study and requires the user's decision.
5. Use `explore` and `edit` to read inputs and prepare ordinary Workspace RQAlpha strategy files. Review completed-bar signals, look-ahead, missing data, board lots and position semantics. The strategy owns signals and orders; the runner owns costs, matching, holdings evolution and execution policy. Use `exec_command` for file preparation, syntax/pure-calculation checks, result statistics and plots.
6. Call `run_backtest` through normal approval for a representative small run; inspect its returned summary and artifacts before expanding. Repeat the function-tool call for each required parameter combination: there is no batch API, and Agent function tools are not Python/shell functions callable from a script. A large grid is a reason to bound or stage the comparison, not to replace the execution engine.
7. Follow returned paths to verify the saved request, strategy snapshot, summary and relevant tables. Check strategy identity, parameters, interval, source and runtime costs; then compare results. Update the full Todo list when moving from preparation to execution to verification, using these observations as completion evidence.

## Failure and completion boundaries

- A restricted host directory is not a backtest capability failure. Use the built-in inspection/execution tools instead of scanning broader host paths.
- On missing instruments, insufficient warm-up or data coverage, report the exact requested symbols/period and returned error. Do not download, repair or silently substitute data. A different interval, universe or engine can change the research question; establish that change with the user.
- On an actual execution capability gap, retain the attempted tool arguments and error (or the specific unsupported capability) and explain which result cannot be produced. Do not implement a substitute matching, holdings or cost engine in shell.
- Complete a backtest comparison only after traceable `run_backtest` results exist for its claimed combinations, with strategy identity, parameters and saved artifacts verified. A script, a data download or a synthetic calculation alone is not a strategy backtest. Distinguish assumptions, observed metrics and conclusions, and state any untested combinations. Backtests do not establish live execution capability.
