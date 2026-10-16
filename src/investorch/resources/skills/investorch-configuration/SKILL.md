---
name: investorch-configuration
description: Inspect InvestOrch configuration and model status, change normal application settings or MCP connections, and guide operators through model and authorization setup. Use when configuring the application or diagnosing effective policy.
version: 1.2.0
---

# Configure InvestOrch

1. Read `references/configuration.md` for the relevant configuration lifecycle, model, image, MCP, or backtest rules.
2. Use `get_config` as the authority for effective settings. Python Schema defaults, local `<root>/investorch.toml` overrides, and hot runtime changes form the effective configuration. Connections live under `models.<id>` and built-in role selections under `agents.<role>`. Direct operators to Web Setup when required settings or credentials are missing.
3. Use `update_config` for approved normal settings. Validate type/range and whether the key is hot, immutable, or restart-required. For restart-required changes use `persist=true` and report that the running process still uses its prior snapshot.
4. Manage MCP entries with `list_mcp_servers`, `configure_mcp_server`, and `remove_mcp_server`. Their registry is `<root>/mcp.toml`, separate from application MCP policy.
5. Direct operators to Web Setup/Settings for connections, Agent defaults, secrets, ChatGPT authorization and Permission settings, and to System Status or `investorch doctor` for offline diagnosis. Skills pages start normal Agent Sessions; their preset instructions do not grant approval.
6. Report effective versus persisted values and any required restart. Never copy secrets into Skills or Memory. Agent config tools cannot modify `models.*`, `agents.*`, `secrets.*`, `auth.*`, `permission.*`, or existing Session model selections. Operators edit Session Drafts through the conversation controls or `/model` and `/effort`; the next Run commits them, and an active Run stays frozen.

The Skill Review Agent uses `agents.permission` as an independent role. Candidate content is sent to that provider for safety review. Skill registry is state, not application configuration; use Skill management tools for lifecycle changes.

This is managed built-in version 1.2.0. Existing instances adopt it only through explicit `investorch --update`, followed by restart; normal startup preserves the installed copy.
