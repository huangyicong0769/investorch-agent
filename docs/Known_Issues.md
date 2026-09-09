# Known issues

## Historical adjustment factors differ between suppliers

**Observed 2026-09-09 on Windows/MiniQMT with `xtquant==250807.1.2` and `rqalpha==6.3.0`.** Native RQAlpha bundle factors and xtdata corporate-action factors can produce different adjusted prices for the same raw bars. This remains a data-source limitation when comparing native backtests with live history.

The live history contract preserves native cumulative factors through each worker's frozen coverage cutoff, then multiplies in later xtdata `dr` events. Merged raw bars receive one RQAlpha adjustment pass. This preserves RQAlpha `pre`/`post` price and inverse-volume computation for the supplied inputs; it does **not** promise numerical equality between suppliers. No conversion is applied to force xtdata factors to match native values.

### Measured examples

Each row compares an actual xtdata event with the ratio of adjacent native cumulative factors. Daily bars around the action and native dividend records were inspected separately. Displayed native ratios are rounded to twelve decimal places for readability.

| Instrument | Action date | xtdata `dr` | Native cumulative-factor ratio |
| --- | --- | ---: | ---: |
| 600000.SH | 2024-07-18 | 1.036697 | 1.036819997556 |
| 000001.SZ | 2023-06-14 | 1.024369 | 1.024811705763 |
| 300750.SZ | 2023-04-26 | 1.811822 | 1.811833625080 |
| 600519.SH | 2026-06-26 | 1.023663 | 1.023668134744 |
| 300750.SZ | 2026-04-22 | 1.016355 | 1.015674405763 |
| 000002.SZ | 2022-08-25 | 1.062659 | 1.061896086525 |

Several observations are consistent with different ex-price rounding conventions; that explanation is an **inference**, not a universal provider formula:

- For 600000.SH, the prior close was 9.04 and the per-share dividend was 0.321. The native ratio is near `9.04 / 8.719`; xtdata `dr` is near `9.04 / 8.72`.
- For 000001.SZ, the prior close was 11.77 and the dividend was 0.285. The native ratio is near `11.77 / 11.485`; xtdata `dr` is near `11.77 / 11.49`.
- For 300750.SZ on 2023-04-26, the prior close was 385.90, the dividend was 2.52, and the transfer was 0.8 shares per share. The native ratio is near the unrounded theoretical reference price; xtdata `dr` is near the reference price rounded to 212.99.

The larger differences are not explained by this observation alone. For 300750.SZ on 2026-04-22, both raw sources reported a prior close of 447.60 and an action-day previous close of 440.69, while xtdata `dr` implies approximately 440.3973. For 000002.SZ on 2022-08-25, `dr` implies approximately 15.6400 while the observed previous close was 15.65. Their full upstream causes remain unresolved. Native cumulative factors were stored as float64, but the inspected values had about six significant digits; this does not justify a blanket tolerance for the larger differences.

### Interpreting comparisons

In all six samples, the change in the SDK `back_ratio` price multiplier matched `dr`, confirming the event-multiplier direction for these observations. SDK adjusted volume remained unchanged, whereas RQAlpha inversely adjusts volume. SDK `front_ratio` also reflected actions after some requested end dates. An unnormalized SDK adjusted series is therefore not an interchangeable RQAlpha oracle.

Use two separate checks:

1. Compare the hybrid calculation with native RQAlpha using the **same raw bars and cumulative factors**. This checks source composition and adjustment semantics. The [factor-cache tests](../packages/investorch-qmt/tests/test_fresh_factors.py) exercise that boundary.
2. Compare actual supplier observations separately, retaining the action date, raw bars, origin date, and each supplier's factor values. Report numerical differences without relabeling them as algorithm failures or silently widening tolerances.

The [official xtdata documentation](https://dict.thinktrader.net/nativeApi/xtdata.html) describes the corporate-action fields and `get_divid_factors`, but does not specify a complete cross-supplier equivalence rule. Strategy authors should retain the live provider's factors, use explicit adjustment modes, and investigate materially different signals before assuming native and live results are interchangeable. See the [live runtime contract](RQAlpha_Live_Runtime_Model.md#daily-data-and-event-contract) for the supported source policy.

Historical price adjustment does not implement dividend cash delivery, bonus-share delivery, rights subscriptions, tax, or reconciliation with broker and Ledger balances.

### Related raw-data precision

Observed xtdata daily volume was reported in integer hands, converted to shares by multiplying by 100; native stock volume retained individual-share precision. Sample differences were less than 50 shares. Some turnover and index prices also had different decimal precision. These observations are separate from factor composition and do not establish a general error bound or authorize fabricated precision.
