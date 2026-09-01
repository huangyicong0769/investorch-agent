# Changelog

[简体中文](CHANGELOG.zh-CN.md)

## 0.1.0

The first public preview of InvestOrch Agent.

### Added

- Web and Textual TUI clients backed by one application and runtime, plus a diagnostic plain console.
- Persistent sessions with archive, restore, fork, clear, context compaction, and concurrent runs across sessions.
- Steer and Queue follow-up modes, manual and model-assisted approval, usage reporting, and durable conversation journals.
- Workspace-scoped file, command, calculation, Todo, configuration, and MCP registry tools.
- RQAlpha daily stock backtesting with reproducibility metadata and workspace-relative artifacts.
- Optional CNEquity CLI, data-overlay, and read-only MCP integration.
- Bootstrap workspace synchronization through `--sync` and `--sync-force`.

### Distribution

- GitHub Release wheel and source distribution with SHA-256 checksums.
- Apache-2.0 licensing for InvestOrch Agent code and documentation, plus bundled third-party notices and Web dependency licenses.

See the [0.1.0 release notes](docs/releases/0.1.0.md) for installation requirements, current scope, and known boundaries.
