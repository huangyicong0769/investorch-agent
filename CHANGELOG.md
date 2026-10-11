# Changelog

[简体中文](CHANGELOG.zh-CN.md)

## 0.2.0 (unreleased)

D/E management and recovery work below is implemented on the feature branch; final integration, real-Agent and recovery acceptance remains pending.

- Replace packaged default TOML with strict Python Schema semantics, provider-neutral new installs, atomic configuration updates/reset and one-time historical model-default migration with an original-file backup.
- Add Web Setup before Agent startup, persistent Settings with backend field metadata, and separate process-local future-Run overrides.
- Add Skills catalog/details and Create/Install/Edit/Fork Session entry points while preserving independent review and approval.
- Add shared, credential-safe MCP integration management; changes apply after restart.
- Add read-only System Status, `doctor` and `upgrade check`, including managed built-in differences; `--update` remains explicit and avoids Portfolio initialization.
- Add offline Backup Create/Verify with default credential exclusion and operator recovery guidance; Portfolio v6 migration remains separate.

- Add end-to-end image conversations, Workspace image reading through explore, inline history and gated external image display.

- Add six versioned Workspace Skills, registration/provenance, startup discovery, and on-demand loading.
- Add independent candidate safety review and approved installation, removal, and enablement workflows.
- Replace product-guide synchronization with deterministic `investorch --update` for managed built-ins; customization uses forks.
- Move configuration, Portfolio, and RQAlpha methodology out of Memory/Core prompts. Add Big QMT authoring guidance for human deployment.

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
