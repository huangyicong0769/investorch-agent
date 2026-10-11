---
name: investorch-configuration
description: Inspect or change InvestOrch configuration, models, secrets, MCP connections, or restart-required settings. Use when configuring the application or diagnosing effective policy.
version: 1.1.0
---

# Configure InvestOrch

1. Read `references/configuration.md` for the relevant configuration lifecycle, model, image, MCP, or backtest rules.
2. Use `get_config` as the authority for effective settings. Python Schema defaults, local `<root>/investorch.toml` overrides, and hot runtime changes form the effective configuration. New installations choose their models through Web Setup; existing installations migrate historical model defaults to explicit overrides.
3. Use `update_config` for approved normal settings. Validate type/range and whether the key is hot, immutable, or restart-required. For restart-required changes use `persist=true` and report that the running process still uses its prior snapshot.
4. Manage MCP entries with `list_mcp_servers`, `configure_mcp_server`, and `remove_mcp_server`. Their registry is `<root>/mcp.toml`, separate from application MCP policy.
5. Direct operators to Web Settings for secrets and Permission settings, and to System Status or `investorch doctor` for offline diagnosis. Skills pages start normal Agent Sessions; their preset instructions do not grant approval.
6. Report effective versus persisted values and any required restart. Never copy secrets into Skills or Memory. Agent config tools cannot modify secrets, `permission.*`, or `models.permission.*`; those are operator-owned settings.

The Skill Review Agent uses the existing Permission model configuration as an independent role. Candidate content is sent to that provider for safety review. Skill registry is state, not application configuration; use Skill management tools for lifecycle changes.

This is managed built-in version 1.1.0. Existing instances adopt it only through explicit `investorch --update`, followed by restart; normal startup preserves the installed copy.
