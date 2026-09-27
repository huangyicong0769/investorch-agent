---
name: investorch-configuration
description: Inspect or change InvestOrch configuration, models, secrets, MCP connections, or restart-required settings. Use when configuring the application or diagnosing effective policy.
version: 1.0.1
---

# Configure InvestOrch

1. Read `references/configuration.md` for the relevant configuration lifecycle, model, image, MCP, or backtest rules.
2. Use `get_config` as the authority for effective settings. Bundled TOML defaults, local `<root>/investorch.toml` overrides, and runtime-only changes form the configuration layers; Memory does not supply current values.
3. Use `update_config` for approved normal settings. Validate type/range and whether the key is hot, immutable, or restart-required. For restart-required changes use `persist=true` and report that the running process still uses its prior snapshot.
4. Manage MCP entries with `list_mcp_servers`, `configure_mcp_server`, and `remove_mcp_server`. Their registry is `<root>/mcp.toml`, separate from application MCP policy.
5. Report effective versus persisted values and any required restart. Never copy secrets into Skills or Memory. Agent config tools cannot modify secrets, `permission.*`, or `models.permission.*`; those are operator-owned settings.

The Skill Review Agent uses the existing Permission model configuration as an independent role. Candidate content is sent to that provider for safety review. Skill registry is state, not application configuration; use Skill management tools for lifecycle changes.
