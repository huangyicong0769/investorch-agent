# Model connections and Session models

[简体中文](Configuration.zh-CN.md)

This guide describes the 0.3.1 configuration.

## Two configuration layers

- `models.<id>` registers a named API-key or ChatGPT connection.
- `agents.<role>` selects the connection, model ID, reasoning effort and context window. The roles are `main`, `title`, `activity`, `permission` and `compact`. Skill Review uses `permission`. Spawn inherits its parent Run’s settings.

The optional `[agents.title]` model generates a title for a Session still unnamed after its Main answer completes. When that role is unset, the Title Agent uses the model selected for the Run. A title returned by the Main model is saved immediately and skips the auxiliary request.

Connection IDs use letters, digits, `_` and `-`. Reasoning effort accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh` and `max`; the chosen model must support the requested level. API-compatible connections allow manual model IDs. ChatGPT choices come from the selected account's catalog, and requests use its model slug.

The effective context window is a positive integer. Supply an explicit window when setting an explicit Main model ID. An auxiliary role on the same connection and model can inherit Main’s window. Changing a Session’s model ID requires its window. Changing the connection also requires the name and effort. Use the window from the provider’s documentation or catalog.

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

## DeepSeek Flash

Choose **DeepSeek API key** in Web Setup or Settings. The Responses adapter recognizes `type = "deepseek"` and existing OpenAI-compatible connections to `https://api.deepseek.com` or `https://api.deepseek.com/v1`. A new Agent on this connection defaults to `deepseek-flash` and a context window of 1,048,576 tokens.

```toml
[models.deepseek]
type = "deepseek"

[agents.main]
model = "deepseek"
reasoning_effort = "high"

[agents.activity]
model = "deepseek"

[agents.permission]
model = "deepseek"

[agents.compact]
model = "deepseek"

[secrets]
DEEPSEEK_API_KEY = "replace-locally-with-your-api-key"
```

| Capability | Configuration and availability |
| --- | --- |
| Web search and URL reading | Main Flash text requests enable `web_search` and `read_url` by default. Both are off for auxiliary Agents by default. Set them per supported role in **Agent Defaults** or for the next Run through the Session model menu. In TUI/plain, `/model web off` disables both for the next Run. |
| Auto title | Flash can return a title in the Main response to a user Run in an unnamed Session. InvestOrch saves the first usable title immediately. Set `auto_title = false` under `[agents.main]` to disable automatic naming. |
| Content safety | `safety_check = "high"` enables DeepSeek input and output review for Flash text requests. `"low"` selects the API’s default checks. |
| Pro and legacy models | For `deepseek-v4-pro`, **Web search** enables the standard Responses search tool and its progress events. Flash extensions also apply to the legacy Flash aliases on a direct DeepSeek connection. |

Copying Main settings in **Agent Defaults** preserves the auxiliary roles’ web switches. The Title Agent names Sessions from conversation history and omits web tools from its requests.

With thinking off, a Run with client tools omits native web search, URL reading, native title requests and explicit safety review. Enabling thinking lets Flash use these features alongside local tools. A request without client tools can use non-thinking search. Structured-output requests use the standard Responses format.

DeepSeek Responses is stateless, so each request carries its context. InvestOrch replays images in their original local tool results. While a tool image is still in the request, Flash defers native web search, URL reading and titles, even after the model has read the image. Native web tools resume automatically when a later request has no tool images and the model settings allow them. User attachments can still use native web. High content safety review requires text-only local tool results.

Web and TUI use the returned events to update each tool call. The `kind` field distinguishes Flash search, page reading and image search. Usage shows billed operations and reported safety tokens. The provider decides when to search and may charge for tools used before a failure. [DeepSeek Web Tools](https://api-docs.deepseek.com/guides/web_tools) · [Models and pricing](https://api-docs.deepseek.com/quick_start/pricing/)

Main continuation with DeepSeek Responses reuses the original web calls with their IDs, `kind` and `signature`. Compact retains the latest signed web round when it fits the input budget. The Title Agent receives portable conversation history with DeepSeek-specific web items and reasoning removed.

Files, OCR, ASR and TTS can be called through `DeepSeekServiceClient.from_config(config, "deepseek")` as an async context manager. Use `upload_file`, `list_files`, `get_file` and `delete_file` to manage files. `input_file(file_id)` creates the Responses content part for text and parsed documents. Use `ocr`, `transcribe`, `synthesize` and `stream_speech` for the standalone services. Uploads default to a one-hour expiry. `stream_speech` accepts an async iterable of text chunks and yields audio frames.

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

For a ChatGPT-only installation, choose an authorized ChatGPT connection for all four roles; an API key is unnecessary. Different credentials represent separate account/Workspace authorizations. Connections deliberately sharing a credential share its refresh lock and token rotation. ChatGPT connections use the official endpoint; they do not accept a custom `base_url` or API-key reference.

Main can use native **Web search** when the selected ChatGPT model and account support it. It uses the existing `web_search` setting, which defaults to on for Main. Auxiliary Agents default to web off. Set the Main default in **Agent Defaults**. For the next Run, use **Web search** in the Session model menu or `/model web on|off` in TUI and plain. The conversation shows search progress and source links when the model uses the tool. [OpenAI preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)

## Setup and Settings

1. Open `investorch web`. In **Model Connections**, add a connection and save it. Setup runs before the Agent Host starts.
2. For an API connection, paste the key into the masked **API key** field on the connection card. To choose a different secret name for Local TOML, expand **Advanced key settings**. For ChatGPT, select **Continue with ChatGPT** and authorize plan usage in the browser. Cancelling retains existing authorization. Reauthorize when the account lacks plan access or its authorization has expired or been revoked.
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

Each new Run receives messages, function `call_id` links, and compatible web calls with their IDs. Prior opaque reasoning and other response-item IDs stay in storage. Reasoning produced within a Run stays available for its tool continuations. See [Runtime](Runtime_Session_Execution_Model.md) for execution details.

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

`backtest.max_log_preview_chars` is a positive integer (default `32768`) limiting the native warning/error
preview returned by each backtest tool call. It hot-applies to the next call; `preview_truncated` signals
omitted preview text. The complete run log remains in `artifacts.execution_log`, and the same diagnostics
are saved in `artifacts.diagnostics`. Failed runs retain their inputs and log paths in error feedback.

Doctor and upgrade checks are offline. They validate structure and credential presence, not current token validity or a successful model request. Managed Skill updates still require explicit `investorch --update` followed by restart.
