---
name: investorch-configuration
description: Inspect InvestOrch configuration and model status, change normal application settings or MCP connections, and guide operators through model and authorization setup. Use when configuring the application or diagnosing effective policy.
version: 1.3.2
---

# Configure InvestOrch

1. Read `references/configuration.md` for the relevant configuration lifecycle, model, image, MCP, or backtest rules.
2. Use `get_config` with specific dotted leaf keys, such as `runtime.max_turns` or `images.default_detail`, as the authority for effective settings. Section names such as `backtest` are not readable leaf keys; use `key=null` only when the full effective configuration is necessary. Python Schema defaults, local `<root>/investorch.toml` overrides, and hot runtime changes form the effective configuration. Connections live under `models.<id>` and built-in role selections under `agents.<role>`. Direct operators to Web Setup when required settings or credentials are missing.
3. Use `update_config` when the user requests changes to normal application settings or defaults. A single backtest or comparison uses `run_backtest.parameters` for execution assumptions; its null fields inherit `backtest.*` defaults without changing AppConfig. Read `rqalpha-strategy` for those controls. Validate type/range and whether a configuration key is hot, immutable, or restart-required. For restart-required changes use `persist=true` and report that the running process still uses its prior snapshot.
4. For each update, inspect `persisted`, `applied`, and `requires_restart`, then read the same effective key again. A saved restart-required value can differ from the current effective value; preserve that distinction. A failed update leaves the requested change unconfirmed.
5. Manage MCP entries with `list_mcp_servers`, `configure_mcp_server`, and `remove_mcp_server`. Their registry is `<root>/mcp.toml`, separate from application MCP policy.
6. Direct operators to Web Setup/Settings for connections, Agent defaults, secrets, ChatGPT authorization and Permission settings, and to System Status or `investorch doctor` for offline diagnosis. Skills pages start normal Agent Sessions; their preset instructions do not grant approval.
7. Report effective versus persisted values and any required restart. Never copy secrets into Skills or Memory. Agent config tools cannot modify `models.*`, `agents.*`, `secrets.*`, `auth.*`, `permission.*`, or existing Session model selections. Operators edit Session Drafts through the conversation controls or `/model` and `/effort`; the next Run commits them, and an active Run stays frozen.

Read [the complete example](references/examples.md) before changing normal settings or explaining saved versus running values. It includes both hot and restart-required changes, readback, and a final answer. For MCP changes, follow the registry lifecycle in [configuration reference](references/configuration.md): list, mutate through the dedicated tool, list again, then report restart status.

The Skill Review Agent uses `agents.permission` as an independent role. Candidate content is sent to that provider for safety review. Skill registry is state, not application configuration; use Skill management tools for lifecycle changes.

This is managed built-in version 1.3.2. Existing instances adopt it through explicit `investorch --update`, followed by restart; normal startup preserves the installed copy.
