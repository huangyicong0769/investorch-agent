# MiniQMT historical suspension integration — 2026-09-09

## Implementation and evidence boundary

The production candidate `f13e427d7d5f786e5d449ce286062a0742dda7e1`, based on `5d795a9d76c975d370c90c67b8d36ebb6c85b87c`, passed real supported-scope qualification. All 46 installed Python modules matched the candidate hashes. The [Phase A report](QMT_Suspension_Gap_Probe_2026-09-09.md) records the anchored provider experiment that preceded production changes.

Both real maintenance runs published `historical_data=READY`, `native_through=2026-08-31` and `fresh_through=target_through=2026-09-08`. The original 13-stock, 46-date readiness blocker is resolved in this pinned qualification. This is a warm-cache qualification after Phase A's targeted downloads, not a claim that the entire universe was downloaded again from an empty cache.

The reader first validates the explicit raw response for the requested symbol. Complete raw ranges return directly. For missing CS dates, the resolver requires a preceding cached provider price and a complete same-exchange index trading-date axis. A provider-filled missing row is accepted only with its exact native date, explicit `suspendFlag=1`, zero volume and amount, finite flat OHLC and `preClose` matching the preceding provider close. Existing raw observations must agree with the matrix. Floating comparison permits at most four binary ULPs, reflecting the measured rounding between the pinned API shapes, rather than a market-price tolerance.

Only maintenance post-validation may ensure unavailable history. It downloads the stock's preceding anchor range once, revalidates, and downloads the required index range once only if its axis is incomplete. Every targeted request retains pacing and STOP checkpoints. The final read repeats full validation with downloads disabled. Strategy reads, initial validation and earliest-gap searches remain read-only. Successful downloads, missing anchors, malformed responses and weak filled evidence cannot promote coverage.

The actual provider supplies prices; the index supplies dates. No stock whitelist or manual suspension calendar enters production. The real missing-normal-data negative control remains unverified, as disclosed in Phase A. The strong signature and normal controls support this pinned qualification but do not prove universal vendor classification accuracy.

## Native behavior and rejection checks

The native bundle remains the prefix source. An accepted completed suspension row is returned by `get_bar` and retained by `history_bars(skip_suspended=False)`; the default CS filter skips zero-volume rows and backfills the requested count. Adjustment still occurs once after the native/fresh merge.

The deterministic native HDF5 comparison passes 49 checks: `get_bar` plus 48 combinations of suspension filtering, `none`/`pre`/`post`, common/scalar fields and counts 1/5. Both sides use the same adjustment origin. The separate real native oracle in Phase A covers 144 history queries and eight `get_bar` calls across three native suspension cases.

Public behavior tests reject absent or invalid flags, nonzero volume/amount, nonflat or unanchored prices, nonfinite fields, malformed or mismatched matrix dates, incomplete/abnormal index axes, and malformed raw payloads. A reported successful ensure without usable observations remains incomplete. Complete raw history remains available when fill/download services are unavailable. STOP before or after a targeted request propagates without publishing success. Adapter, maintenance and native parity checks pass together: 130 tests.

An independent Spec review found that a malformed raw payload could previously be treated as empty and hidden by valid fill. The regression was reproduced before correction; `f13e427` now requires a dict containing an explicit DataFrame for the requested symbol. Legitimate empty DataFrames remain supported. Independent Spec and Standards reviews have zero remaining findings at this candidate.

## Validation

The Windows environment retained xtquant `250807.1.2`, RQAlpha `6.3.0` and Dongguan MiniQMT `2.0.10`. The qualification clock was explicitly fixed to September 9 at noon so the last completed target remained September 8. Actual execution timestamps are retained separately in the evidence.

| Real check | Result |
| --- | --- |
| Original 13-stock cases | All 46 missing dates accepted; six completed rows per stock. Every gap passed `get_bar`; both history filtering modes returned the requested count. |
| Native cases through an artificial source cutoff | Three actual native suspension dates retained matching history selection; raw numerical differences were at floating rounding scale, with zero volume/amount differences. The bundle itself was not truncated. |
| First real `HistorySyncManager` | READY in 25.484 seconds; 20 subsequent polls stable, no new child process. |
| Independent full cache audit | 5,792 checked, zero failures, 34,751 expected rows including 46 suspension rows; 24.625 seconds. Listing dates determine expected row counts. |
| Second fresh manager instance | READY in 25.328 seconds; another 20 stable polls and clean child shutdown. |
| Supported scope | 7,349 candidates; 5,792 supported; 1,557 exclusions unchanged: 981 unavailable canonical mappings and 576 unknown provider instruments. |
| Native protection | File sizes and modification timestamps unchanged across each real qualification; no native bundle update was invoked. |

The ready status is from the actual manager snapshot after its supported-scope validation, not a status inferred from the diagnostic reader. Read-only cache validation and both manager runs used the final production modules. Earlier `e779db9` runs are preliminary and are not the acceptance evidence.

- Core: 541 passed; existing dependency warnings only.
- Companion on macOS: 524 passed, 11 Windows-specific skips.
- Core and companion Ruff formatting/lint: passed.
- Core wheel and source distribution: built; both isolated artifact smokes passed.
- Windows companion tests, native xtquant smoke and distribution smokes: pending final package checks.
- Final CI must run against the final branch commit after qualification documentation. Its exact SHA and run URL accompany the delivery, rather than reusing a predecessor's green run.

## Scope and review

The global watermark contract is unchanged: all supported instruments must cover every expected completed trading date. Unsupported provider mappings retain their explicit exclusions; missing data never excludes a supported instrument. The original bundle is not updated by this work.

The optional stalled-batch watchdog was deferred as a separate reliability change. Existing pacing remains. No Tushare, suspension database, manual suspension symbol list, missing-equals-suspension heuristic, global watermark relaxation, Core backtest change or B4 expansion was introduced.

Changes remain on `codex/qmt-suspension-gap-probe` for user review. This qualification does not authorize merging the branch.

Local evidence is retained under `.git/b35-validation/suspension-plan/`: `phaseb-source-verified.json`, `production-13-gap-f13e427.json`, `production-manager-f13e427.json`, `production-cache-f13e427.json`, `production-manager-repeat-f13e427.json`, their exclusion metadata reports, and the two qualification scripts. Phase A observations and prior failed/preliminary attempts remain separate and recoverable.
