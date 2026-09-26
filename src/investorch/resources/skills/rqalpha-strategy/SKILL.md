---
name: rqalpha-strategy
description: Design, write, review, debug, or run RQAlpha strategies and interpret backtest artifacts. Use when working with strategy lifecycle, data availability, or backtest runtime semantics.
version: 1.0.0
---

# RQAlpha strategy workflow

1. Read `references/runtime.md` before specialized work; it defines the supported pinned daily stock runtime and canonical examples.
2. Establish instruments, period, signal timing, execution assumptions, and relevant user/project decisions. Use `get_config` for current policy. Native bundle inspection and CNEquity research are separate authorities; inspect native coverage with `inspect_rqalpha_data` when available, otherwise follow the configured overlay contract.
3. Write an ordinary Workspace Python strategy using supported lifecycle and daily APIs. Review completed-bar timing, look-ahead, missing data, and lot/position semantics before execution. Runtime policy belongs to the runner.
4. Use `run_backtest` through normal approval. Surface coverage errors rather than automatically downloading or repairing datasets. No minute/tick/auction logic is supported by this runtime.
5. Start from the compact result summary and inspect saved artifacts only as needed. Distinguish assumptions, observed metrics, and conclusions; retain request metadata and strategy identity for reproducibility. A backtest is not evidence of live execution capability.
