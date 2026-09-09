# MiniQMT historical suspension gap probe — 2026-09-09

## Phase A result

The anchored experiment meets the supplementary plan's Phase A gate for continuing integration in the pinned environment. It does **not** establish production readiness or prove that every missing row is a suspension. No production reader behavior changed during this phase.

All 13 known suspension cases retained their 46 raw missing dates after targeted download. With a cached preceding price observation and a second, normally trading security supplying the matrix time axis, `get_market_data(fill_data=True)` returned all 46 dates with exact timestamps, `suspendFlag=1`, zero volume/amount and OHLC equal to the preceding provider close and `preClose`. Single-security reads supplied only seven missing dates. The companion therefore cannot obtain the full observed behavior by changing `get_local_data`'s fill flag alone.

A real non-suspension missing-data control has **not** been established. Three targeted existing-cache candidates were checked without downloading: 600519.SH, 300750.SZ and 000002.SZ each already had all 110 trading dates from April 1 through September 8. Their complete caches cannot test misclassification of absent normal observations. Normal-row controls and announcement concordance are useful but do not replace that negative control. The plan permits reporting this limitation; the result must not be described as eliminating every possible false positive.

## Environment and execution

- `xtquant==250807.1.2`; `rqalpha==6.3.0`; Dongguan Securities MiniQMT 2.0.10.0.
- Production baseline: `5d795a9d76c975d370c90c67b8d36ebb6c85b87c`; all 45 installed Python module hashes matched it.
- Successful probe: September 9, 14:23:58–14:24:20 Asia/Shanghai; process exited normally.
- Fresh target: September 1–8, all completed dates. Every expanded query began August 18, ten native trading sessions before the fresh start, and included a valid preceding price observation.
- Two normal controls ran first: 600000.SH and 000001.SZ. Each supplied a verified complete normal raw axis for its exchange.
- The pinned single-symbol `download_history_data` implementation was inspected before use; its default path calls synchronous `supply_history_data`. A one-second pause separated targeted requests. A separate diagnostic watchdog could exit only its own Python process; it did not claim cancellation of MiniQMT server work.
- The first attempt stopped before any download because the normal control also appeared as its own axis symbol. The public report regression reproduced this duplicate-index problem; commit `9d9313d` deduplicated the request. Its failed receipt remains separate from the successful run.

## All 13 cases

Dates below are in 2026. Each price is the common OHLC and `preClose` for the accepted-pattern rows, rounded only for this table; full observations retain the provider's original floating values. Every listed filled row has volume=0, amount=0 and suspendFlag=1. Price anchors are listed from the native reference; the probe separately retained and validated the actual provider observation preceding each gap.

| Symbol | Gap form | Missing dates | Native preceding traded date | Filled OHLC / preClose |
|---|---|---|---|---|
| 000016.SZ | trailing | 09-04, 09-07, 09-08 | 2026-08-31 | 2.46 |
| 600929.SH | entirely_empty | 09-01, 09-02, 09-03, 09-04, 09-07, 09-08 | 2026-08-28 | 6.04 |
| 300472.SZ | internal | 09-02 | 2026-08-31 | 7.22 |
| 002870.SZ | entirely_empty | 09-01, 09-02, 09-03, 09-04, 09-07, 09-08 | 2026-08-31 | 53 |
| 600825.SH | trailing | 09-07, 09-08 | 2026-08-31 | 5.31 |
| 688291.SH | trailing | 09-08 | 2026-08-31 | 44.4 |
| 002743.SZ | internal | 09-07 | 2026-08-31 | 4.89 |
| 002274.SZ | leading | 09-01 | 2026-08-25 | 6.47 |
| 002731.SZ | entirely_empty | 09-01, 09-02, 09-03, 09-04, 09-07, 09-08 | 2026-08-31 | 0.77 |
| 301266.SZ | leading | 09-01, 09-02, 09-03, 09-04 | 2026-08-28 | 24.97 |
| 002998.SZ | trailing | 09-04, 09-07, 09-08 | 2026-08-31 | 8.6 |
| 301139.SZ | entirely_empty | 09-01, 09-02, 09-03, 09-04, 09-07, 09-08 | 2026-08-28 | 3.31 |
| 688432.SH | entirely_empty | 09-01, 09-02, 09-03, 09-04, 09-07, 09-08 | 2026-08-28 | 45.22 |

## Behavior by gap form

| Form | Cases / missing days | Single-security local or matrix fill | Matrix with normal axis security |
|---|---:|---|---|
| Internal | 2 / 2 | Both supplied the two dates. | Same valid field pattern. |
| Leading | 2 / 5 | With the preceding observation inside the expanded window, both supplied five dates. | Same valid field pattern. |
| Trailing | 4 / 9 | Neither extended the single-security axis through the missing tail. | All nine dates supplied. |
| Entire fresh range empty | 5 / 30 | Download supplied the earlier anchor, but single-security reads still did not extend through the target range. | After the anchor ensure, all thirty dates supplied. |

The five entirely empty cases changed after the ensure because their pre-gap provider observations were previously absent. The other ten cases, including the two controls, had unchanged observations across the before/after ensure comparison. Every repeated read was stable. No `suspendFlag=-1` resumption row was observed; it is not used as a prerequisite.

The normal rows retained their timestamps and flags. Local/matrix comparisons found 282 floating representation differences, all within 2e-15 relative / 1e-12 absolute comparison bounds. Maximum price difference was 7.11e-15 and maximum turnover difference 4.77e-7. These are recorded observations, not a license to normalize away substantive differences or alter the provider's raw fields.

## Required Phase A report checklist

| Items from supplementary plan §54 | Evidence / result |
|---|---|
| 1–2: SDK and client versions | Pinned environment above; source/identity receipts. |
| 3–5: Instruments, missing dates, classification | Complete 13-row table above, 46 security-days. |
| 6: Raw read | All 46 dates absent before and after ensure. Actual existing row fields retained. |
| 7–8: Local and matrix fill | Same seven dates for single security; paired matrix supplies all 46. |
| 9–10: Anchor and targeted download | August 18 expanded start; five empty cases needed preceding observations downloaded; remaining reads unchanged. |
| 11–15: Flag, volume, amount, OHLC, preClose | Every paired missing-date row has flag=1, zero amounts, exact timestamp and anchored consistent prices; full raw values retained. |
| 16: Reopen -1 | Not observed. |
| 17–18: Normal controls and false positives | Two complete normal controls remained normal; repeated stable reads. Real non-suspension missing control not found; limitation explicit. |
| 19: API choice | Raw local read remains preferred. Only paired matrix fill supplied trailing and empty ranges; any integration must retain and validate this axis dependency. |
| 20: Gate | Go for bounded integration under §18/§20, with negative-control limitation retained. This is not a production/full-scope PASS. |

## Native suspension oracle

A separate process read the original bundle without importing xtquant or modifying native files. Three known native suspension cases were queried: 000016.XSHE on August 24, 002274.XSHE on August 26 and 301266.XSHE on August 31. Across 144 history queries and eight `get_bar` calls:

- `get_bar` returned a structured previous-close-price bar with zero volume and turnover, rather than None.
- `skip_suspended=False` retained the suspended date. True removed it and fetched earlier traded rows to satisfy counts 1 and 3.
- Fields=None, common-field lists and scalar close/volume were exercised under none/pre/post. Post adjustment materially changed prices.
- The established seven-common-field companion contract remains unchanged; the native oracle's extra limit fields do not widen it.

Integration still needs deterministic native/fresh-side parity tests and a real full supported-scope rerun. A successful diagnostic does not promote the global watermark.

## Reproduction and evidence

The reusable [probe script](../../packages/investorch-qmt/scripts/probe_suspension_history.py) accepts explicit symbols or a small sample JSON, date range and native trading-day anchor lookback. It is read-only by default; `--ensure-history` opts into targeted cache downloads. It refuses to overwrite its output and records pinned API source plus a script hash. Candidate-field indicators are diagnostic observations, not a production classifier.

Local review artifacts are under `.git/b35-validation/suspension-plan/`: `baseline-gap-summary.json`, `probe-attempt1.jsonl`, `probe-attempt2.jsonl`, `probe-summary.json`, `source-verified.json`, `run_probe_guarded.py`, `native-suspension-oracle-result.json`, `native-suspension-oracle-notes.md`, `existing-negative-control.json` and `xtquant-smoke.log`. They contain small targeted observations, not a submitted market-wide dataset. The earlier announcement audit is `suspension-announcement-evidence-e1e8e8c.md`; announcements remain external experimental checks and never enter production data flow.

The [official API documentation](https://dict.thinktrader.net/nativeApi/xtdata.html) defines forward filling and suspension fields. The recorded pinned behavior and controls determine the scope of this result; the documentation alone does not establish classification accuracy for absent normal data.

## Scope boundary

No Tushare, announcement ingestion, suspension database, manual suspension whitelist, missing-equals-suspended rule, watermark relaxation, Core backtest change or B4 change was introduced. The optional maintenance watchdog enhancement is deferred as a separate reliability task. Existing batch pacing remains intact. Known Issue closure awaits production validation, not this report alone.
