# Changelog

[简体中文](CHANGELOG.zh-CN.md)

## 0.3.0 — unreleased

### Session collaboration and model selection

- Add bounded Session discovery/history tools, approved text/image messaging with sender provenance, and recursive child Sessions with independent approvals and a shared Run limit.
- Confirm the full Spawn descendant scope before Delete/Clear, preserving active work and independent Fork references.
- Split model connections from Agent defaults, add official local ChatGPT authorization, and support per-Session model/effort Drafts with immutable selections for active Runs.
- Add preflight and between-tool-turn compaction, preserve outstanding work through repeated compaction, and keep prior-Run history portable across model connections.

### Runtime feedback and workflows

- Show live Main/auxiliary usage and provider reasoning summaries; persist redacted Run failures and deliver approval rejection feedback to the Main Agent.
- Preserve Web approvals, drafts and navigation through refresh/reconnect/resize; expose orphaned background processes and distinguish diagnostic problems from unchecked state.
- Isolate MCP connection failures from Runs and retain actionable initialization causes.
- Clarify built-in specialist toolchains and artifact validation. Add typed per-run RQAlpha parameters, serialized execution, native logs and original engine rejection diagnostics.

### Compatibility and distribution

- Refresh Python/Web dependencies and lockfiles, including OpenAI Agents SDK 0.24.0, FastAPI 0.161.1, RQAlpha 6.4.2, optional CNEquity 0.18.0 and uv_build 0.13.5.
- Prepare PyPI wheel/source distribution alongside GitHub artifacts. Publication remains pending final review and approval.
- **Breaking:** old `models.<role>` TOML requires manual conversion to `models.<connection>` plus `agents.<role>`. Session model metadata upgrades through normal initialization; existing Roots require backup and preflight before startup.

See the [0.3.0 release preparation notes](docs/releases/0.3.0.md) and [Upgrade](docs/Upgrade.md). This entry does not announce published artifacts or completed release acceptance.

## 0.2.0

### Portfolio and approval

- Add Portfolio lifecycle, immutable economic Ledger entries, deterministic exact-decimal holdings and cash projections, single-base-currency accounting, and explicit unknown cost.
- Add dedicated Portfolio SQLite persistence, approved Agent Tools for initialization, trades, cash flows, income, corrections and atomic internal transfers, and read-only Web Portfolio views.
- Add New Portfolio/Ask Agent workflows and durable Session-to-Portfolio relations preserved through archive, restore and fork.
- Review complete active user instruction history at a frozen turn boundary. Compact oversized authorization history, and require manual review if compaction fails. Require grounded material accounting facts and explicitly supplied fees.
- Adopt Portfolio schema v6 with account attribution; preserve existing accounting facts through backed-up migration. Schemas v3–v5 require explicit migration after legacy live deployments are stopped.

### Skills

- Add six versioned Workspace Skills, registration/provenance, startup discovery, and on-demand loading.
- Add independent candidate safety review and approved installation, removal, and enablement workflows, plus Web catalog/details and Create/Install/Edit/Fork Session entry points.
- Remove `--sync` and `--sync-force` product-guide synchronization; use deterministic `investorch --update` for managed built-ins and forks for customization. User Memory is preserved.
- Move configuration, Portfolio, and RQAlpha methodology out of Memory/Core prompts. Add Big QMT authoring guidance for human deployment.

### Image conversations

- Add end-to-end image conversations, Workspace image reading through `explore`, inline history, and click-to-load external images.

### Setup, settings and recovery

- Replace packaged default TOML with strict Python Schema validation, explicit provider configuration, and atomic configuration updates/reset. Existing local configuration must match the current Schema.
- Add Web Setup before Agent startup, persistent Settings with backend field metadata, and separate process-local future-Run overrides. Restart-required preferences and replaced credentials take effect after restart.
- Add credential-safe MCP integration management. Changes apply after restart; clearing a Web timeout restores the application default.
- Add read-only System Status, `doctor` and `upgrade check`, including managed built-in differences. `--update` remains explicit and avoids Portfolio initialization.
- Add offline Backup Create/Verify, default credential exclusion, and manual recovery into a separate root. Portfolio v6 migration remains separate.
- Keep Web recovery guidance available when existing storage, configuration or enabled Skills prevent safe application startup.

### Compatibility

- Refresh Python runtime/tooling and Web dependencies and their lockfiles, including RQAlpha 6.4.1. Keep the optional CNEquity integration pinned to 0.7.3.
- Allow ApplicationHost startup on Linux while keeping Agent `exec_command` restricted to macOS.
- Keep Portfolio writes approval-gated, Web Portfolio pages read-only, and QMT deployment human-operated. Live order execution and broker reconciliation are outside this release.

See the [0.2.0 release notes](docs/releases/0.2.0.md) for installation and upgrade instructions.

## 0.1.0

The first public preview of InvestOrch Agent.

### Added

- Web and Textual TUI clients backed by one application and runtime, plus a diagnostic plain console.
- Persistent sessions with archive, restore, fork, clear, context compaction, and concurrent runs across sessions.
- Steer and Queue follow-up modes, manual and model-assisted approval, usage reporting, and durable conversation journals.
- Workspace-scoped file, command, calculation, Todo, configuration, and MCP registry tools.
- RQAlpha daily stock backtesting with reproducibility metadata and workspace-relative artifacts.
- Optional CNEquity CLI, data-overlay, and read-only MCP integration.

### Distribution

- GitHub Release wheel and source distribution with SHA-256 checksums.
- Apache-2.0 licensing for InvestOrch Agent code and documentation, plus bundled third-party notices and Web dependency licenses.

See the [0.1.0 release notes](docs/releases/0.1.0.md) for installation requirements, current scope, and known boundaries.
