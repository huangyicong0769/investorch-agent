# 模型连接与 Session 模型

[English](Configuration.md)

本文适用于 0.3.1。

## 两层配置

- `models.<id>` 注册命名的 API key 或 ChatGPT 连接。
- `agents.<role>` 选择连接、真实模型 ID、推理强度与上下文窗口。内置角色包括 `main`、`title`、`activity`、`permission` 和 `compact`。Skill Review 共用 `permission`，Spawn 默认继承父 Run。

`[agents.title]` 可选，用于在主回答完成后为仍无标题的 Session 命名。未配置时，Title Agent 使用该 Run 的模型。主模型一旦返回有效标题，界面立即更新并省去辅助请求。

连接 ID 只使用字母、数字、下划线和连字符。推理强度支持 `none/minimal/low/medium/high/xhigh/max`，具体模型也必须支持所选等级。API 连接允许手填真实模型 ID；ChatGPT 从所选账号目录选择，请求使用目录 slug。

有效窗口始终是正整数。Main 必须明确填写；辅助角色只有在连接与真实模型都与 Main 相同时才能省略并继承。Session/Spawn 不改变模型时可继承窗口；改变模型名必须重新指定窗口；跨连接必须同时指定名字、effort、窗口。窗口来自模型文档或可信目录，应用不会根据名称猜测。

## API key 完整示例

下列配置可通过结构校验。调用前请替换 key、模型 ID 与窗口。`128000` 是示例值。

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

在 Web Setup 或 Settings 选择 **DeepSeek API key**。Responses 适配器识别 `type = "deepseek"`，也识别 API 地址为 `https://api.deepseek.com` 或 `https://api.deepseek.com/v1` 的既有 OpenAI-compatible 连接。新 Agent 选择直连 DeepSeek 时，默认模型为 `deepseek-flash`，上下文窗口为 1,048,576 tokens。

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

| 能力 | 配置和可用性 |
| --- | --- |
| 原生搜索和 URL 阅读 | Main 的 Flash 文本请求默认启用 `web_search` 和 `read_url`。所有辅助 Agent 默认关闭两项。可在 **Agent Defaults** 按角色设置。Session 模型菜单的改动在下一次 Run 生效，TUI/plain 可用 `/model web off` 为下一次 Run 关闭两项。 |
| 自动标题 | 未命名 Session 的 Flash 用户 Run 可在 Main 请求中获取标题。首个有效标题立即保存。可在 `[agents.main]` 下设 `auto_title = false` 关闭自动命名。 |
| 内容审核 | `safety_check = "high"` 启用 Flash 文本请求的输入与输出审核。`"low"` 使用 API 默认检查。 |
| Pro 与旧模型 | `deepseek-v4-pro` 使用标准 Responses。声明 `web_search` 时，服务端通过标准搜索事件报告进度。直连 DeepSeek 时，Flash 的旧别名也可使用 Flash 扩展。 |

在 **Agent Defaults** 复制主模型设置时，各辅助角色保留自己的联网开关。Title Agent 始终使用会话历史生成标题，请求省略联网工具。

思考关闭且请求包含本地工具时，请求会省略原生联网、原生标题及显式安全审核参数。开启思考后可将这些 Flash 能力与本地工具一起使用。没有本地工具的请求支持非思考搜索。结构化输出使用标准 Responses 格式。

Web 和 TUI 根据返回的事件更新同一条工具调用的状态，并用 `kind` 区分 Flash 的搜索、网页读取和图片搜索。用量面板显示 DeepSeek 返回的计费次数和审核 Token。是否联网由 DeepSeek 决定，失败前执行的操作也可能计费。 [DeepSeek 联网文档](https://api-docs.deepseek.com/guides/web_tools) · [官方定价](https://api-docs.deepseek.com/quick_start/pricing/)

DeepSeek Responses 的 Main 续接会回传原始联网调用及其 ID、`kind` 和 `signature`。存在带签名的联网轮次时，Compact 保留最近一轮。Title Agent 的输入先转换为可移植会话历史，去除 DeepSeek 专用联网条目和推理记录。

在 Python 中以异步上下文管理器调用 `DeepSeekServiceClient.from_config(config, "deepseek")`。文件接口为 `upload_file`、`list_files`、`get_file` 和 `delete_file`，上传默认一小时过期。文本文件或已解析文档用 `input_file(file_id)` 构造 Responses 内容项。独立服务接口为 `ocr`、`transcribe`、`synthesize` 和 `stream_speech`。`stream_speech` 接收异步文本分块，返回音频帧。

## ChatGPT 多账号与混合连接

下例包含两个独立 ChatGPT 连接和一个 API 连接。替换账号目录 slug、API 模型 ID、key 和示例窗口。登录前结构合法，但引用的 ChatGPT 账号必须完成计划额度授权后才能推理。OAuth 凭据由应用在授权成功后写入；不要手造 Token 记录或复制其他应用的 Token。

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

仅使用 ChatGPT 时，四个角色均选择已授权的 ChatGPT 连接即可，无需 API key。不同 credential 对应不同账号/Workspace 授权；明确共享同一 credential 的连接共用刷新锁与 Token 轮换。ChatGPT 使用官方 Endpoint，不接受自定义 `base_url` 或 API key 引用。

## Setup 与 Settings

1. 启动 `investorch web`，在 **Model Connections** 新建并保存连接。此时 Agent Host 可以尚未启动。
2. 将密钥粘贴到连接卡片的 **API key** 输入框，输入会自动遮盖。如需自定义本地 TOML 中的密钥名称，展开 **Advanced key settings**。ChatGPT 连接点击 **Continue with ChatGPT**，在浏览器里授权使用计划额度。取消会保留原授权。缺少计划额度权限或授权过期、撤销时，可重新授权。
3. 在 **Agent Defaults** 选择真实模型与窗口。可将 Main 配置复制给辅助角色，再逐一调整 effort。
4. **Test saved connection** 发送一次短请求，可能消耗 API 或 ChatGPT 计划额度。**Save and open InvestOrch** 在 readiness 通过后启动同一个 Host。

Settings 复用连接与 Agent 编辑器。连接定义、Agent 默认值、API key 的修改需重启；OAuth 登录、注销和按需刷新立即生效。注销清除可续期授权，但保留连接及会话数据；删除连接则另行检查 Agent、Session 持久绑定、Draft、活动 Run 和待处理工作引用。

连接旁显示账号授权状态与 **Manage Usage**。目录读取失败时，应检查授权并重试，不能据此判断账号没有可用模型。额度耗尽或账号不合格不会自动切换账号或转用付费 API。临时刷新故障保留可恢复凭据，撤销授权需重新登录。凭据私有保存在 Local TOML，公开配置、浏览器读取和 Agent 输出均不返回明文。

账号资格及服务端规则参见 OpenAI 的[本地登录说明](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)、[模型与推理](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)、[错误恢复](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)。

## Session 模型控制

Web 输入框右下显示模型与思考强度。点选模型或在 **Thinking** 中选择强度后，立即用于下一次 Run。**Context window** 单独设置窗口；新模型没有已配置窗口时，先填写其支持的上限，再点击 **Use model**。**Manage models** 打开连接设置。TUI/plain 使用：

```text
/model
/model openai_api replace-with-provider-model-id high 128000
/effort
/effort low
/model login chatgpt_personal
/model logout chatgpt_personal
```

解析优先级为 Draft → 最近持久配置 → 新建或旧版未绑定 Session 的 Main 默认值。Draft 影响下一次 Run，包括 Queue 晋升时；Steer 保持当前 Run 模型。未启动 Run 的 Draft 只在进程内保存，退出会丢失。编辑已归档 Session 的 Draft 不会解除归档。

每个 Run 冻结连接、模型、effort 和正整数窗口。用户输入先记 Journal，然后持久化模型选择，再允许推理；这是不同存储边界，失败时可能留下已记录但未执行的输入。Fork 复制来源的有效模型，未提交设置作为目标 Draft 保留；Spawn 默认继承父 Active Run，显式覆盖只影响新子 Session。

如果目标窗口容纳不下 continuation 与输入，Main 推理前先压缩；失败或仍无法容纳时不执行 Main。回答后的自动压缩仍保留。两种压缩均保留完整 JSONL Journal。

每次新 Run 的请求副本会剔除旧 continuation 中不透明 reasoning 和 Provider response-item ID，保留消息及函数 `call_id` 关联；SQLite/Journal 原件不变，本 Run 新生成的 reasoning 仍可继续使用。即使连接未变也执行这一处理，因此不会跨 Run 复用旧 opaque reasoning，可能影响连续性或缓存效率。执行契约见 [Runtime](Runtime_Session_Execution_Model.zh-CN.md)。

## 操作者边界

Agent Tools 可读取脱敏配置，并经审批修改普通设置；`models/agents/secrets/auth/permission` 及已有 Session 模型由操作者管理，Agent 配置工具不能修改。配置只有两层，不增加 `providers` 或 `agents.spawned`。0.3.0 不将 InvestOrch 作为 MCP server 对外提供服务，已有 MCP client 集成继续保留。

Doctor/升级预检是离线检查，只证明结构与凭据存在性，不证明 Token 当前有效或模型已经调用成功。内置 Skill 更新仍需显式 `investorch --update` 并重启。

## 工具读取预算

文件/搜索、Session 历史、Portfolio 账本和原生 bundle 检查的读取限制由 AppConfig 管理。
Settings 从后端 Schema 展示这些设置，Local TOML 只需保存显式覆盖：

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

这些设置在下一次工具调用时热生效。`limit` 为 null 或省略时继承有效默认值，显式值按有效上限校验；
一次读取使用同一个配置快照。默认条数须不超过最大条数，Session 字段预算须不超过总响应预算。
无效更新保持内存有效配置与 TOML 文件原状。

Session 总预算按包含转义的序列化字符计量，字段预算按原文字符计量。完整记录、显式省略元数据和续读游标
保留原有语义。文件/搜索设置继续位于 `explore.*`，原生标的检查使用 `backtest.inspect_max_symbols`。
已有显式覆盖继续生效。

`backtest.max_log_preview_chars` 为正整数，默认 `32768`，限制每次回测工具返回的原生警告/错误摘要字符数，
下一次调用热生效。`preview_truncated` 表示摘要尚有省略；完整日志保存在 `artifacts.execution_log`，
同一份诊断信息保存在 `artifacts.diagnostics`。失败运行也保留输入和日志，错误反馈会给出对应工件路径。
