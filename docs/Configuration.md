# Model connections and Session models

[简体中文](Configuration.zh-CN.md)

This guide describes the **unreleased 0.3.0** configuration. Released 0.2.0 installations must follow [Upgrade](Upgrade.md) before using it.

## Two configuration layers

- `models.<id>` registers a freely named **connection**: an OpenAI-compatible endpoint and API-key reference, or a ChatGPT authorization reference.
- `agents.<role>` selects that connection, the actual model ID, reasoning effort and context window. The built-in roles are `main`, `title`, `activity`, `permission` and `compact`. Skill Review shares `permission`; Spawn inherits its parent Run instead of using another role.

Connection IDs use letters, digits, `_` and `-`. Reasoning effort accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh` and `max`; the chosen model must support the requested level. API-compatible connections allow manual model IDs. ChatGPT choices come from the selected account's catalog, and requests use its model slug.

The effective context window is always a positive integer. Main needs an explicit window. An auxiliary role may omit it only when its connection and actual model match Main. A Session or Spawn override may inherit an unchanged model's window; changing the model name requires a new explicit window. Changing the connection requires the name, effort and window together. Obtain the window from the model's documentation or account catalog; the application does not guess it from the name.

## API-key example

This is a complete model/Agent configuration with structurally valid placeholders. Replace the key, model ID and example window before making requests. `128000` is an example, not a statement about any model's capacity.

```toml
[models.openai_api]
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_key_secret = "OPENAI_API_KEY"

[agents.main]
model = "openai_api"
name = "replace-with-provider-model-id"
reasoning_effort = "high"
context_window_tokens = 128000

[agents.title]
model = "openai_api"
name = "replace-with-provider-model-id"
reasoning_effort = "none"
context_window_tokens = 128000

[agents.activity]
model = "openai_api"
name = "replace-with-provider-model-id"
reasoning_effort = "none"
context_window_tokens = 128000

[agents.permission]
model = "openai_api"
name = "replace-with-provider-model-id"
reasoning_effort = "high"
context_window_tokens = 128000

[agents.compact]
model = "openai_api"
name = "replace-with-provider-model-id"
reasoning_effort = "high"
context_window_tokens = 128000

[secrets]
OPENAI_API_KEY = "replace-locally-with-your-api-key"
```

## ChatGPT accounts and multiple connections

This example configures two independent ChatGPT connections and one API-key connection. Replace both catalog slugs, the API model ID, key and example windows. It is schema-valid before login; inference remains unavailable until each referenced ChatGPT account has authorized plan usage. The application writes the OAuth credential records after successful authorization; do not invent token records or copy another application's tokens.

```toml
[models.chatgpt_personal]
type = "chatgpt"
credentials = "chatgpt_personal"

[models.chatgpt_work]
type = "chatgpt"
credentials = "chatgpt_work"

[models.api]
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_key_secret = "API_KEY"

[agents.main]
model = "chatgpt_personal"
name = "select-personal-account-catalog-slug"
reasoning_effort = "high"
context_window_tokens = 128000

[agents.title]
model = "api"
name = "replace-with-provider-model-id"
reasoning_effort = "none"
context_window_tokens = 128000

[agents.activity]
model = "api"
name = "replace-with-provider-model-id"
reasoning_effort = "none"
context_window_tokens = 128000

[agents.permission]
model = "chatgpt_work"
name = "select-work-account-catalog-slug"
reasoning_effort = "high"
context_window_tokens = 128000

[agents.compact]
model = "chatgpt_personal"
name = "select-personal-account-catalog-slug"
reasoning_effort = "high"
context_window_tokens = 128000

[secrets]
API_KEY = "replace-locally-with-your-api-key"
```

For a ChatGPT-only installation, choose an authorized ChatGPT connection for all five roles; an API key is unnecessary. Different credentials represent separate account/Workspace authorizations. Connections deliberately sharing a credential share its refresh lock and token rotation. ChatGPT connections use the official endpoint; they do not accept a custom `base_url` or API-key reference.

## Setup and Settings

1. Open `investorch web`. In **Model Connections**, add a connection and save it. Setup runs before the Agent Host starts.
2. For an API connection, enter the write-only API key. For ChatGPT, select **Continue with ChatGPT**, complete the browser authorization yourself and allow ChatGPT plan usage. Identity-only login is insufficient. Cancel leaves existing authorization intact; reauthorization handles missing permission or expired/revoked credentials.
3. Select the model and window under **Agent Defaults**. **Copy Main settings to auxiliary roles** is optional; review each role's effort afterward.
4. **Test saved connection** makes one short request and can consume API or plan usage. **Save and open InvestOrch** starts the same Host after readiness validation.

Settings uses the same connection and Agent editors. Connection/default/API-key changes require restart. OAuth login, logout and on-demand refresh apply without restart. Logout removes renewable authorization while retaining the connection and conversation data; deletion separately checks Agent, persisted Session, Draft, active Run and pending-work references.

ChatGPT account status and **Manage Usage** are visible beside the connection. If the catalog fails, retry that lookup after checking authorization; a failed lookup does not mean the account has no models. Usage limits and unavailable plan access do not cause a switch to another account or paid API connection. Temporary refresh errors retain recoverable credentials; revoked authorization requires signing in again. Credentials are stored privately in Local TOML and omitted from public configuration, browser reads and Agent output.

See OpenAI's [local sign-in guide](https://developers.openai.com/siwc/token-sharing-open-source/sign-in), [models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference), and [errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery) for account eligibility and provider behavior.

## Session model controls

The control at the bottom right of the Web composer shows the model and thinking level. Select a model or open **Thinking** to change the level; selections apply immediately to the next Run. **Context window** opens the window setting. When a new model has no configured window, enter its supported limit before choosing **Use model**. **Manage models** opens connection settings. TUI and plain use:

```text
/model
/model openai_api replace-with-provider-model-id high 128000
/effort
/effort low
/model login chatgpt_personal
/model logout chatgpt_personal
```

Draft precedence is Draft → last persisted selection → Main default for an unused/legacy Session. A Draft affects the next Run, including a queued message when promoted. Steer keeps the current Run's model. Drafts are process-local and disappear if the application exits before a Run commits them. Editing an archived Session's Draft does not unarchive it.

Each accepted Run freezes the connection, model, effort and positive window. Its input is journaled before the model selection is persisted and before inference. These are separate storage boundaries; failure can leave recorded input that has not executed. Fork copies the source's effective model selection, keeping uncommitted changes as a target Draft. Spawn inherits the parent's active model by default; explicit overrides affect only the new child.

When the selected window is too small for continuation plus input, preflight compaction runs before Main inference. If it fails or the input still cannot fit, Main does not run. Post-answer automatic compaction remains available. Both preserve the complete JSONL Journal.

At each new Run, the request copy of prior continuation omits opaque reasoning and provider response-item IDs while retaining messages and function `call_id` links. SQLite and Journal originals remain intact; new reasoning within that Run remains available. This also applies when the connection is unchanged: previous opaque reasoning cannot be reused across Runs, which may change continuity or caching efficiency. See [Runtime](Runtime_Session_Execution_Model.md) for execution details.

## Operator ownership

Agent Tools can inspect redacted configuration and request approved changes to normal settings. `models`, `agents`, `secrets`, `auth` and `permission` are operator-owned; Agent config updates cannot edit them or existing Session model selections. Model configuration has no `providers` layer and no `agents.spawned` role. 0.3.0 does not expose InvestOrch as an MCP server; existing MCP client integrations continue.

## Tool reading budgets

AppConfig owns file/search, Session history, Portfolio Ledger and native bundle inspection limits.
Settings exposes them from the backend schema; Local TOML contains only explicit overrides.

```toml
[session_tools]
default_page_size = 200
max_page_size = 500
max_response_chars = 262144
max_field_chars = 131072

[portfolio_tools]
default_ledger_limit = 200
max_ledger_limit = 1000
```

These values hot-apply to the next tool call. A null or omitted `limit` inherits the effective default;
an explicit value is checked against the effective maximum. Each read captures one configuration snapshot.
Defaults must be at or below their maximum; the Session field budget must be at or below its response budget.
Invalid updates leave both effective configuration and the TOML file unchanged.

Session response budgets count serialized characters including escaping; field budgets count original
text characters. Complete records, explicit omission metadata and continuation cursors retain their
existing meaning. File/search budgets remain under `explore.*`, and native inspection uses
`backtest.inspect_max_symbols`. Existing explicit overrides remain effective.

Doctor and upgrade checks are offline. They validate structure and credential presence, not current token validity or a successful model request. Managed Skill updates still require explicit `investorch --update` followed by restart.
