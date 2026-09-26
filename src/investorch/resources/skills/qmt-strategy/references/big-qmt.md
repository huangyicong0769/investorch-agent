# Big QMT API and timing reference

Verified against the vendor's built-in Python documentation on 2026-09-26. Broker builds can lag the public manual; qualify the target client before choosing optional functions.

## Lifecycle and state

QMT injects `ContextInfo` into `init` and `handlebar`; do not instantiate a substitute or import RQAlpha callbacks. Historical processing traverses bars, while a forming live bar can cause repeated calls. `is_last_bar()` identifies the latest bar, not a once-only event or a completed bar. ContextInfo state has platform-specific bar commit behavior; ordinary Python state and persisted files also need explicit restart semantics. Establish a stable intent identity and reconcile outstanding orders before resubmission. Source: [vendor lifecycle guide](https://docs.thinktrader.net/pages/5d9ffa/) and [vendor FAQ](https://dict.thinktrader.net/innerApi/question_answer.html).

## Data

`ContextInfo.get_market_data_ex(fields=[], stock_code=[], period='follow', start_time='', end_time='', count=-1, dividend_type='follow', fill_data=True, subscribe=True)` returns a symbol-keyed mapping. Explicitly choose `1d` or the required minute period, a historical cutoff, adjustment, and fill behavior. `subscribe=False` reads local history without subscribing. An empty end time can reach latest data, so bind historical queries to the simulated bar time. `ContextInfo.get_full_tick` supplies current snapshots, not a history series. Document the required local data before handing off. Source: [vendor market-data API](https://dict.thinktrader.net/innerApi/data_function.html).

## Orders and account reads

The built-in global order API is `passorder(opType, orderType, accountid, orderCode, prType, price, volume, strategyName, quickTrade, userOrderId, ContextInfo)`. Verify operation, price, quantity unit, and asset-specific enums against the client's manual. `quickTrade=1` can submit on a current bar invocation; `2` can submit even during historical traversal. Neither is a duplicate-order guard. Submission is asynchronous and returns no fill confirmation. Query account/position/order/deal records through `get_trade_detail_data` for the explicitly selected account/type, and use `order_callback`/`deal_callback` evidence as appropriate. Handle rejection, partial fills, cancellation, and stale account views. Broker data remains distinct from InvestOrch logical Portfolio truth. Source: [vendor trading API](https://dict.thinktrader.net/innerApi/trading_function.html).

## Review checklist

- Confirm whether each signal uses a completed bar or deliberately uses a forming bar; include warmup and session-end behavior.
- Separate signal intent from submission, acknowledgment, and fill. A successful function return is not execution evidence.
- Define behavior after client disconnect/restart and after uncertain submission; never retry blindly.
- Account and position queries require actual client connectivity and may update asynchronously. Report observed timestamps and unknown state.
- Keep imports compatible with the target embedded Python. Source syntax checks on a different interpreter are partial evidence.
- Keep user deployment and activation explicit. This reference does not establish a live integration or execution capability in InvestOrch.
