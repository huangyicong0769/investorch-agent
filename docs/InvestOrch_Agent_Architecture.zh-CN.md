# InvestOrch Agent 架构

[English](InvestOrch_Agent_Architecture.md)

## 范围

本文描述当前源码架构。未来产品方向记录在[产品路线图](InvestOrch_Agent_Product_Roadmap.zh-CN.md)。

## 系统上下文

```text
                   用户
              /             \
          Web 客户端       Textual TUI
              \             /
               Application Host
                      |
              Application services
                      |
                 AgentRuntime
                      |
              OpenAI Agents SDK
                      |
              Main 与辅助 Agents
                /             \
          内置 Tools           MCP servers
                |
       Workspace / RQAlpha / 本地状态
```

plain console 以顺序执行的诊断界面连接同一个 Application Host。

## 组合与所有权

`application.host.open_application_host()` 是 Web、TUI 和 plain 模式共享的 composition boundary。它创建并关闭：

- 已验证的 `AppConfig`；
- 保存未来 Run 默认值和选中 Session 状态的 `AppState`；
- Application 全局 `ExecutionState`；
- `SessionJournal` 与 Session operations；
- OpenAI Responses models 与 Agent definitions；
- MCP server manager；
- 审批、Activity label、presentation 与 Runtime coordinators。

Host 拥有进程生命周期资源。Run 不拥有 Workspace、MCP manager 或受管后台进程。

## Presentation 层

### Web

`investorch web` 在 `127.0.0.1` 运行 FastAPI application。Server 通过 REST 提供 Session 与交互状态、分页 Journal 历史、默认值、Queue、压缩和审批操作，通过 WebSocket 传输 live application events。React 前端使用这些 Application 接口，并以 bootstrap 返回的 Web 配置作为 UI defaults 的来源。

管理入口包括 `/skills`、`/skills/:name`、`/settings`、`/system` 和 `/setup`。新实例在 Agent Host 建立前即可提供 Setup 和只读诊断。Setup 经 AppConfig 保存模型字段及凭据，可由用户主动发起小型模型测试，随后启动 Host。旧配置或存储不兼容时进入恢复状态，而不是重新初始化。

`web.security` 在浏览器 HTTP 边界检查 loopback Host、浏览器同源和 JSON 写请求。非浏览器本地客户端可省略 Origin 与 Fetch Metadata；管理接口禁止缓存。该边界独立于 Agent Tool 审批，也不提供经过认证的远程服务。

### TUI

`investorch` 启动 Textual 客户端。它与 Web 使用相同的 Application services 和 Runtime callbacks。Session 选择、Timeline projection、Composer、Queue、Todo、审批、进程和用量展示属于 Presentation，不拥有执行状态。

### Plain console

`investorch --plain` 运行顺序式诊断客户端。它显示 raw output 并使用 inline approval，不提供 Web/TUI 的并发交互界面，也不运行 Activity Agent。

`presentation.py` 提供 live Web events 与持久化 Journal history 共享的 transport-neutral JSON-safe projections。

## Session、Run 与交互模型

Application 分离三个 identity：

- **Session**：持久化对话 identity。
- **Run**：一次瞬时顶层 Agent turn。
- **Selection**：客户端当前显示的 Session。

`AgentRuntime` 拥有 active Runs 和 follow-up queues。每个 Session 最多一个顶层 Run，不同 Session 可以并发。每个 Run 捕获不可变 `AgentModelConfig`（连接、真实模型、effort、窗口）、permission mode 与 follow-up 设置。Session Draft 由 Host 统一持有；Queue 仅在晋升时解析最新 Draft，提交时捕获的 permission/follow-up 默认值保持不变。

Steer 在安全的 turn boundary 继续当前顶层 Run。Queue 保存未来意图，并在成功完成后提升为新 Run。Stop 取消选中 Session 的 active Run，并暂停保留的 Queue 意图。

详细不变量见 [Runtime / Session 执行模型](Runtime_Session_Execution_Model.zh-CN.md)。

## Agent 集成

InvestOrch 使用 OpenAI Agents SDK，不自行实现 Agent loop 或 Tool-call protocol。`AgentLoop` 在 SDK Run 周围增加 streaming output、approval continuation、title、usage、compaction 和 Steer continuation 等 Application 行为。

Main Agent 会为每个 Run 使用已捕获 model settings 进行 clone。辅助 Agent 职责狭窄：

- Title Agent 生成 Session 标题。
- Activity Agent 为 Tool call 生成只用于展示的 label。
- Permission Agent 在 review 模式下可以返回 approve、reject 或 ask。
- Compact Agent 用带标记的 summary 替换 SDK continuation history。

Runtime 保留 OpenAI Agents SDK 与 Responses adapter。`models.<id>` 定义可复用连接，`agents.<role>` 为五个内置角色选择连接、真实模型、effort 与正整数窗口；分别由 `AppConfig.model_connection()`、`agent_model()` 解析。Skill Review 共用 `agents.permission`。Host 的 `ModelConnectionClients` 按连接复用 Client，停机统一关闭；每个 Run 克隆自己的模型及设置，不修改共享 Agent 模板。

ChatGPT 连接使用验证后的本地 OAuth 授权与官方 direct Responses Endpoint。薄适配层发送流式、不存储请求，限制不支持的字段，并通过流式结果收集服务 SDK 的非流式调用者。Host 持有 `ChatGPTAuth`，Web Setup 在 Agent Host 未启动时也能授权。凭据按需刷新，同 credential 串行化，并与 Settings 共用私有 TOML 原子写回；公开配置不返回 Token。详见[Configuration](Configuration.zh-CN.md)。

## 当前 Tool Surface

Main Agent 当前获得：

- Workspace 与执行：`explore`、`edit`、`delete`、`exec_command`；
- Utility 与状态：`calculate`、`get_current_time`、`write_todos`；
- Session 发现与历史：`list_sessions`、`read_session`；
- Session 输入提交：`send_message`（需要发送方审批）；
- 配置：`get_config`、`update_config`；
- MCP registry：`list_mcp_servers`、`configure_mcp_server`、`remove_mcp_server`；
- 回测：`run_backtest`，以及选择原生 bundle 时的 `inspect_rqalpha_data`。

Tool 直接使用 Agents SDK Tool definitions。Portfolio Tool 委托 PortfolioOperations，Skill Tool 委托 SkillOperations，Session 只读 Tool 委托 `session_queries.py` 中的无状态 Application 查询函数；adapter 不直接写入持久化状态。

会修改 Workspace 或执行代码的能力强制实施 Workspace 边界与审批策略。Tool failure 以明确异常返回。

## Session 访问（0.3.0 Stage A）

只读 Session 访问遵循以下边界：

```text
Agent Session Tools（Host 已绑定的查询）
├── Application list_sessions -> Storage metadata + Runtime.session_snapshot(target_id)
└── Application read_session  -> Storage 存在性检查 + 已有 SessionJournal + 共享 Steer 事实
```

`application.session_queries.list_sessions` 和 `read_session` 是显式接收依赖、不保存隐式读取位置的普通 async 函数。Host 按 `Agent -> AgentLoop -> AgentRuntime` 顺序构造后，通过 `functools.partial` 绑定查询，并在 yield Host 前向已有 Agent 一次追加 `make_session_tools(...)`。每个 Run 的 Agent clone 都能看到完整 Tool 列表，Run 期间无需重新注册。每个 Host 分别绑定自己的数据库、Runtime 和已有 Journal，历史查询不需要 Runtime。Tool 仅向已绑定的查询转发经过验证的公开参数。共享 Journal 事实为历史读取和 Review 解析 Steer 激活/丢弃证据，Review 保留严格的指令边界策略。不增加历史数据库或 Runtime 状态机。

`list_sessions(query=None, include_archived=False, limit=200, offset=0)` 枚举当前 Root 的元数据。空 query 不过滤标题；非空 query 仅在 title 上进行 Unicode `casefold()` 子串匹配，不支持 Session ID 或消息正文筛选。默认排除归档 Session；`include_archived=True` 同时包含归档与未归档 Session。每行返回 `session_id`、`title`、`archived`、`branch_from_session_id`、`created_at`、`updated_at` 和 `status`，按 `updated_at` 降序排列，时间相同时按 `session_id` 排序。`has_more` 为真时用 `next_offset` 继续。Offset 分页反映当前列表，并发变化时为尽力枚举，不冻结列表快照。

有 active Run 时，列表状态为 `running`、`waiting_approval` 或 `stopping`；没有 active Run、但仍有已接受的 Queue 或 Steer 工作时为 `pending`，否则为 `idle`。不返回队列数量、暂停标记或待处理 Steer 详情。归档是独立元数据，历史失败或上一进程的 active Run 不决定当前状态。列表仅读取元数据和当前 Runtime snapshot，不扫描各 Session Journal。

`read_session(session_id, view="conversation", before_seq=None, snapshot_seq=None, limit=200)` 要求当前 Root 中存在的精确 Session ID，可以读取自身、其他正在运行的 Session 或归档 Session。结果只含历史与分页字段，不含 Session metadata、当前状态或上一次 Run 结果。读取不改变 Selection、不启动 Run、不修改 Session。

默认 `conversation` 返回扁平 `user`、`assistant`、`tool_call` 和 `tool_result` 记录，保留原 Journal `seq` 和 `timestamp`。仅已激活 Steer 以普通 `user` 记录显示，序号仍为原始输入序号。Pending/discarded Steer、reasoning、approval 和维护事件不进入此视图。Tool result 独立存在，即使其 call 位于另一页，也不伪造 Tool-call ID。图片仅含经过清理的 `filename`、`media_type`、`detail` 和 `image_count` 元数据，不含 URL 或图片字节。可选 `events` 在响应预算内保留经过验证的原始 Journal 字段和值，包括 reasoning、approval 和图片 payload。

历史分页选择最新的合格记录，按原始序号升序返回。Conversation 的 `limit` 计数可见记录，Events 则计数原始记录。首次读取捕获已提交 Journal head 为 `snapshot_seq`；后续旧页复用该值，将 `next_before_seq` 传为排他的 `before_seq`。后续追加不进入冻结历史。激活/丢弃证据会检查到整个 snapshot，即使证据位于 `before_seq` 之后：`seq=10` 的 Steer 在 `seq=18` 激活时，`before_seq=15, snapshot_seq=18` 仍显示原输入。Snapshot 之后的激活仅在新的读取中可见。

例如，`read_session(session_id="session-b", limit=2)` 可以返回：

```json
{
  "session_id": "session-b",
  "view": "conversation",
  "snapshot_seq": 73,
  "records": [
    {"seq": 70, "timestamp": "2026-10-13T03:00:00+00:00", "type": "tool_result", "output": "Baseline complete."},
    {"seq": 72, "timestamp": "2026-10-13T03:01:00+00:00", "type": "assistant", "text": "The baseline result is ready."}
  ],
  "has_older": true,
  "next_before_seq": 70,
  "omitted_fields": 0
}
```

随后调用 `read_session(session_id="session-b", before_seq=70, snapshot_seq=73, limit=2)`。该页可以返回 `seq=67,69`，追加的 `seq>73` 仍被排除。存在的 Session 若 Journal 缺失或为空，返回 `snapshot_seq=0`、空 records、`has_older=false` 和 `next_before_seq=null`。

两个 Tool 默认每页 200 条、最多 500 条，JSON 编码后的整个结果最多 262,144 字符，包含转义开销。Conversation 可以缩短页面以保留完整记录，保留最新连续合格后缀，使旧页 cursor 可以找回其余记录。单个 `text`、`arguments` 或 `output` 超过 131,072 字符，或该记录编码后超出响应预算时，会显式省略字段而不裁剪。记录保留 identity/metadata，并增加 `omitted_fields` 项，例如 `{"field":"output","reason":"size_limit","original_chars":280000}`；响应级 `omitted_fields` 计数这些项。省略占位记录仍推进分页。连元数据也无法容纳时返回 `response_too_large`。

Raw Events 不会为了预算改写：请求页超限时返回 `response_too_large`，提示减小 `limit` 或使用 Conversation。单个超大 raw event 在该预算下无法通过 Events 获取。无效参数/cursor、缺失 ID、已提交历史损坏、矛盾 Steer 证据或 Journal 不可用均返回明确且安全的错误；有效 Session 缺少 Journal 则为空历史。Snapshot 捕获仅短暂持有 Journal 锁，随后扫描已提交前缀，不申请 Runtime maintenance reservation，也不在历史扫描期间持有写锁。Stage A 不增加跨 Session 发消息或 spawn 能力。

## Session 消息

Host 一次注册 `send_message`，绑定本 Host 的 AppState、Runtime 与已有 Journal。SDK 先在**发送方 Run** 触发现有 Permission/Review/HITL，批准后才调用 `application.messaging.send_message()`。Application 要求目标在当前 Root 精确存在且未归档，拒绝自投，构造普通 `UserInput` 并调用 `submit_user_input()`。投递批准不授予接收方后续 Tool 的权限。

`send_message(target_session_id, text="", images=None)` 接收原始文本和简短图片引用；有图片时文本可为空。引用包含 `source`，以及可空的 `path`、`seq`、`image_index`、`detail`：

- `workspace`：指定 Workspace 相对 `path`。复用路径 containment，拒绝绝对路径及 traversal/symlink 逃出 Workspace；只读普通文件，最多读取 `images.max_image_bytes + 1` 字节。MIME 根据实际字节识别，不信任扩展名。
- `session_event`：指定**发送方自己**已提交历史中的正数 Journal `seq` 与从零开始的 `image_index`。支持用户、已激活 Steer、assistant、tool output 的图片；pending/discarded Steer 不能提供图片。参数不接受 source Session ID。
- 其他来源字段为空或省略。可选 `detail` 覆盖现有图片 detail。两种来源在 Application 内转换为 data URL，复用 `normalize_user_input()` 与已有图片限额；模型无需输出 Base64，也不能抓取任意 URL。

例如，先用 `list_sessions` 获取完整目标 ID，再调用 `send_message(target_session_id="<target-id>", text="请检查这张图", images=[{"source":"workspace","path":"charts/result.png"}])`。纯图转发使用 `send_message(target_session_id="<target-id>", images=[{"source":"session_event","seq":1,"image_index":0}])`。后续用 `read_session` 查询结果，或显式向可信发送 Session ID 回复。

不可变 `UserInput` 携带不可变 `InputOrigin`。外部输入默认 `user`，Tool 从 SDK context 取得 Agent 的 Session/Run ID。Agent 来源作为已有 `user_message`/`user_steer` 的可选元数据，目标 Run ID 保持原含义。SDK 输入仍为 `role=user`，在原始文本/图片前增加独立的应用生成来源说明。Review 在已有冻结边界与 Steer 生效规则下标注有效指示来源。Conversation 仍返回 `type=user`，保留来源和图片元信息；旧历史无需迁移。

Web/TUI 复用普通消息组件，仅增加小型 Agent 来源标记及完整 ID 导航/复制或 resume 提示。通用的 Journal 输入提交后通知接入已有历史同步与 TUI seq 去重。回执只确认 Run/Steer/Queue 接受，[Runtime](Runtime_Session_Execution_Model.zh-CN.md#agent-消息提交)说明各自持久化语义。消息不增加 Mailbox、任务 registry、完成回调、自动回复、spawn 或跨进程传输。并发 Session 仍共享 Workspace 与受管资源。

## Session 派生

Host 将需要审批的 `spawn_agent(task, title?)` 绑定普通 Session 创建与输入提交。子 Session 使用相同 Agent、Tools、Skills、Runtime 和独立 Permission 规则。SQLite Spawn 来源独立于 Fork lineage；关联 Portfolio IDs 只复制一次，不继承历史、Ledger 或授权。Run 完成与 Stop 保持独立。严格正整数、Hot 的 `runtime.max_concurrent_runs`（默认 3）仅在 `_start_run()` 对全部顶层 Run 生效。

Delete 与 Clear 共用受保护的递归 Spawn 删除、全节点 Runtime reservation 和单笔 SQLite 事务。活动/pending 节点和集合外 Fork 引用阻断删除，不自动取消；比较已确认的 Session IDs 与实际范围，防止未经确认扩大范围。提交后清理错误如实报告已删除身份；Clear 再创建普通 replacement。Web 右上紧凑上下文卡片读取 Storage/Runtime/Journal 事实，TUI 增加来源 metadata。接受、恢复与删除契约见 [Runtime](Runtime_Session_Execution_Model.zh-CN.md#session-派生与全局并发)。

## 审批边界

审批在 Application boundary 协调。每个 request 都有不可变 approval ID 以及 Session/Run 所有权。Permission mode 为：

- `manual`：始终询问用户；
- `review`：Permission Agent 能安全决定时使用其结果，否则询问用户。

当前审批保护已配置的 consequential Tools，包括任意 Workspace 命令执行和普通 Python 策略回测。审批是执行授权，不是 Python sandbox。

## Workspace 与后台执行

`ExecutionState` 是 Application 全局状态，包含共享 Workspace sandbox 与受管后台 job。Job 可以比创建它的 Run 存活更久，并保留 owner Session/Run 归属。Session selection 与 Run completion 不会停止 job。

所有 Session 共享一个 Workspace，因此并发 Run 可能操作同一路径；0.1.0 没有 per-Session Workspace 或 filesystem lock。

## 回测

`run_backtest` 验证 Workspace 相对 RQAlpha 策略，捕获一份不可变配置快照，执行日频股票回测，并在配置的 Workspace 目录中写入可复现元数据和 analyser artifacts。

AppConfig 提供执行默认值。类型化 `parameters` 参数可为单次调用覆盖 T+1、分红与税费、价格校验、撮合、交易限制、滑点和交易成本。null 或省略字段继承默认值，明确的 false 和零按覆盖值生效。Tool 解析出一份不可变的有效参数，不修改 AppConfig；返回的 `runtime` 与 `request.json.runtime` 记录同一组值。初始资金与基准仍是独立参数。数据源选择、路径和检查数量限制保留在应用配置中，其中数据源选择也决定启动时的能力组合。单次研究假设通过 Tool 参数表达，修改后续默认值才进入配置流程。

默认数据路径是原生 RQAlpha bundle。当 `backtest.use_cnequity=true` 且已安装可选依赖时，RQAlpha 通过配置字符串加载 `investorch.backtest.rqalpha_mod`，以 CNEquity 日线和复权因子覆盖数据，同时保留 RQAlpha market semantics。

CNEquity 是可选且由用户运维的后端。`investorch data` 把参数传给其 CLI，Application 也可以组合其只读 stdio MCP server。Ingestion、repair、retry、locking 与 recovery 由 CNEquity 自身负责。

## 持久化

配置 root 下的持久化状态按职责分开：

- `<root>/investorch.toml`：本地 overrides 与 secrets；
- `<root>/mcp.toml`：MCP registry；
- `<root>/workspace/`：用户拥有的 Workspace 与生成 artifacts；
- `<state>/sessions.db`：Agents SDK continuation 与 Application Session metadata；
- `<state>/portfolio.db`：Portfolio schema v6；
- `<state>/skills.json`：Skill 注册、启停与来源；
- `<state>/sessions/<session-id>.jsonl`：追加式用户可见 Journal；
- `<state>/logs/investorch.log`：轮转诊断日志。

SQLite continuation 是 model state，可以被压缩；JSONL Journal 是 replay state，不被压缩。Activity label 是 derived annotation，不是执行事实。


## 配置

`config_schema.AppConfigSchema` 是字段类型、默认值、约束、敏感属性和生命周期（`hot`、`restart_required`、`immutable`）的唯一权威。`AppConfig` 合并 Schema defaults、Local TOML overrides 与允许的临时配置变化；运行时不再读取随包默认 TOML。`paths.root` 默认 `~/.investorch`，可通过程序化 bootstrap 注入，Local TOML 不能重定向自身 Root。

`AppConfig` 提供有效/已保存公开视图、readiness、JSON Schema、原子批量修改、通过移除覆盖实现的 reset，以及独立 snapshot。完整候选通过验证后才写盘，保留注释与相邻设置；原子替换与小范围写锁保护落盘，检测到外部修改时拒绝旧视图覆盖。需要重启的修改保存为下次启动值。AppConfig 不识别调用者或执行授权策略：Agent Tool 在边界拒绝修改 `models.*`、`agents.*`、`secrets.*`、`auth.*` 和 `permission.*`，用户直接操作则由 HTTP 边界保护。

所有 Local TOML 均按当前 Schema 校验。模型 Provider 字段须显式配置；缺少模型设置或凭据时进入 Setup。TOML 格式错误、未知字段和无效值保持报错。字段描述与公开视图不包含 Secret 明文。

`AppState` 保留进程级 Permission/follow-up 覆盖，并引用共享 Session 模型存储。`/model`、`/effort` 修改选中 Session 的 Draft，下一次实际 Run 才把纯模型值提交到 Session 元数据，当前 Run 保持冻结。连接与 Agent 默认值重启生效，OAuth 状态即时生效。SQLite 模型元数据不含凭据，普通与 Spawn Session 使用同一契约。

已配置 model 与 MCP endpoints 是外部信任边界。本地优先表示状态和 Workspace 默认由本地拥有，不表示 model 或 MCP traffic 留在本机。

## 依赖方向

```text
Web / TUI / plain
        -> Application services
        -> Runtime 与 Presentation 接口
        -> Agent integration 与 Tools
        -> Workspace、Storage、RQAlpha、MCP、Model endpoints
```

Run 与持久化由 Application 和 Runtime 层拥有。Client command parsing 属于 Presentation。Backtest code 与 UI 和 Session selection 保持独立。

## 当前限制

当前 component 尚未实现：

- Broker 下单、账户镜像、自动持仓监控或实盘交易能力；
- QMT Gateway 或直接 XtQuant 集成；
- 统一投资数据层；
- 自动编排、等待或完成通知；
- 跨进程重启持久化 active Run、pending approval、Steer、Queue 或 Todo；
- 历史 turn conversation branching；
- per-Session Workspace 或跨 Run filesystem locking；
- 经过认证的 LAN 或 remote Web serving。

## Skill Component

Core instructions 保留身份、事实依据、Memory、审批和 Skill 加载规则；专业方法属于六个内置 Skill。Package 仅负责分发，初始化将内置内容写入 `<workspace>/skills/<name>/`，这是唯一 runtime 内容来源。`<state>/skills.json` 仅保存注册、启停和来源；metadata/version 从经过验证的 `SKILL.md` 读取。

ApplicationHost 在启动时固定 enabled catalog 与可加载名称。`load_skill` 的正文/资源通过普通 Tool result 进入 Session history；supporting resources 使用 `explore`，脚本通过 `exec_command` 和现有审批执行。生命周期变更需要重启，同一 host 新建 Session 不会刷新。

安装非内置候选时先验证，再复用 Permission 模型配置进行独立安全审查，审查 Agent 无 Tools。PASS 仍走正常授权，WARN 必须人工审批，BLOCK 禁止安装。专用管理工具修改未来行为；内置 Skill 禁止 Agent 删除或替换，定制需 fork。

`investorch --update` 显式以随包内容替换受管内置 Skill 并保留 enabled，不调用模型或初始化 Portfolio。Doctor/System Status 比较已安装内容与随包内容并报告差异；检测出差异并不自动授权覆盖。普通启动不更新内容。内置缺失/无效导致启动失败并提示更新/重装；外部/自建缺失或无效时排除出 catalog，仍可 inspect。仅在缺失时创建 `MEMORY.md`，已有用户文件保留。

## 管理与恢复的职责

| 入口 | 所有者与行为 |
| --- | --- |
| Settings `/api/config` 与 Schema 描述 | AppConfig 验证并持久化，前端根据后端字段语义决定布局。 |
| Setup `/api/setup` | Host 构造前保存初始模型与 Secret；可选真实请求验证连接。 |
| Skill catalog 与详情 | SkillOperations 读取已安装内容，不加载到 Agent context。 |
| Skill Create / Install / Edit / Fork | SkillSessionWorkflows 带明确操作指令创建 Session，保留正常审查与审批。 |
| MCP `/api/integrations/mcp` | 共享 `mcp.py` 验证、registry 持久化与安全展示；重启生效，未观察到连接时显示 Unknown。 |
| System `/api/system`、`doctor`、`upgrade check` | 共享只读诊断，不调用 Provider、连接 MCP、初始化或迁移。 |
| 离线 Backup Create / Verify | 快照与 manifest 验证；默认排除包含原始凭据的配置。 |

Portfolio 迁移仍由现有模块负责，保留 schema v6 与旧 live 状态显式退役规则；管理层报告所需行动而不自行迁移。恢复由用户在停止应用后向独立 Root 执行，没有通用 Restore CLI 或自动回滚。详见[升级说明](Upgrade.zh-CN.md)和[备份与恢复](Backup_and_Recovery.zh-CN.md)。

## 对话图片内容

`ImageContent` 与不可变 `UserInput(text, images)` 扩展现有对话接口。模型输入统一采用 Responses structured content。用户光栅 data URL 经严格解码、magic bytes 与 `[images]` 限制校验；展示层另外支持 SVG 和 HTTPS。`explore(read)` 返回 SDK 标准文本/图片工具输出，经 Output、Journal、REST、WebSocket 保留图片。Web 与 Markdown 共用点击加载 HTTPS 图片的组件，使用 no-referrer 和图片 CSP，不代理远程内容。

正文中的图表使用 `![净值曲线](figures/nav.png)` 等 Markdown 引用。Workspace 相对路径图片按正文位置显示，工具观察仍默认折叠。只读 `/api/workspace/image` 接口在 Workspace 范围和单图字节限制内提供 JPEG、PNG、GIF、WebP。路径引用展示当前文件，不是 Session 快照：覆盖或删除文件会改变旧消息的显示结果；内嵌图片附件的历史持久化方式不变。

Main、Title、Compact 接收含图片的 SDK 历史。ReviewContext 接受纯图片指令标记，Activity 使用文本/数量摘要；Permission 不从未看到的图片推断授权。持久化副本位于 SDK SQLite 历史与 JSONL Journal，不增加 asset 子系统。
