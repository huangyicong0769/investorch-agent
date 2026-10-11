# InvestOrch Agent 架构

[English](InvestOrch_Agent_Architecture.md)

## 范围

本文描述当前源码架构，包含尚未发布的 0.2.0 管理与恢复工作。D/E 最终验收仍待完成。未来产品方向记录在[产品路线图](InvestOrch_Agent_Product_Roadmap.zh-CN.md)。

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

`AgentRuntime` 拥有 active Runs 和 follow-up queues。每个 Session 最多一个顶层 Run，不同 Session 可以并发。每个 Run 捕获不可变的 reasoning effort、permission mode 和 follow-up 设置。

Steer 在安全的 turn boundary 继续当前顶层 Run。Queue 保存未来意图，并在成功完成后提升为新 Run。Stop 取消选中 Session 的 active Run，并暂停保留的 Queue 意图。

详细不变量见 [Runtime / Session 执行模型](Runtime_Session_Execution_Model.zh-CN.md)。

## Agent 集成

InvestOrch 使用 OpenAI Agents SDK，不自行实现 Agent loop 或 Tool-call protocol。`AgentLoop` 在 SDK Run 周围增加 streaming output、approval continuation、title、usage、compaction 和 Steer continuation 等 Application 行为。

Main Agent 会为每个 Run 使用已捕获 model settings 进行 clone。辅助 Agent 职责狭窄：

- Title Agent 生成 Session 标题。
- Activity Agent 为 Tool call 生成只用于展示的 label。
- Permission Agent 在 review 模式下可以返回 approve、reject 或 ask。
- Compact Agent 用带标记的 summary 替换 SDK continuation history。

Runtime 使用 OpenAI Responses model adapter。Schema 定义五个独立可配置的模型角色，不预设 Provider。Model name、base URL、secret reference 和 reasoning effort 来自 `AppConfig`，构造 Agent 前检查 readiness。独立 Skill Review Agent 复用 Permission 模型配置。

## 当前 Tool Surface

Main Agent 当前获得：

- Workspace 与执行：`explore`、`edit`、`delete`、`exec_command`；
- Utility 与状态：`calculate`、`get_current_time`、`write_todos`；
- 配置：`get_config`、`update_config`；
- MCP registry：`list_mcp_servers`、`configure_mcp_server`、`remove_mcp_server`；
- 回测：`run_backtest`，以及选择原生 bundle 时的 `inspect_rqalpha_data`。

Tool 直接使用 Agents SDK Tool definitions。Portfolio Tool 委托 PortfolioOperations，Skill Tool 委托 SkillOperations；adapter 不直接写入持久化状态。

会修改 Workspace 或执行代码的能力强制实施 Workspace 边界与审批策略。Tool failure 以明确异常返回。

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

默认数据路径是原生 RQAlpha bundle。当 `backtest.use_cnequity=true` 且已安装可选依赖时，RQAlpha 通过配置字符串加载 `investorch.backtest.rqalpha_mod`，以 CNEquity 日线和复权因子覆盖数据，同时保留 RQAlpha market semantics。

CNEquity 是可选且由用户运维的后端。`investorch data` 把参数传给其 CLI，Application 也可以组合其只读 stdio MCP server。Ingestion、repair、retry、locking 与 recovery 由 CNEquity 自身负责。

## 持久化

配置 root 下的持久化状态按职责分开：

- `<root>/investorch.toml`：本地 overrides、secrets 与配置格式标记；
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

`AppConfig` 提供有效/已保存公开视图、readiness、JSON Schema、原子批量修改、通过移除覆盖实现的 reset，以及独立 snapshot。完整候选通过验证后才写盘，保留注释与相邻设置；原子替换与小范围写锁保护落盘，检测到外部修改时拒绝旧视图覆盖。需要重启的修改保存为下次启动值。AppConfig 不识别调用者或执行授权策略：Agent Tool 在边界拒绝 `secrets.*`、`permission.*` 和 `models.permission.*`，用户直接操作则由 HTTP 边界保护。

新安装保留未设置的 Provider 字段并进入 Setup。历史无格式标记的 Local TOML 先备份，再一次性把原隐含模型设置写成显式覆盖；用户已有逐字段覆盖优先。损坏或不兼容配置保持报错。字段描述与公开视图不包含 Secret 明文。

`AppState` 保存可选的进程级 reasoning effort、Permission 和 follow-up 覆盖；无覆盖时读取有效 AppConfig，显式覆盖不被后续配置修改抹掉。`/defaults`、`/effort`、`/permission` 不持久化配置，已启动 Run 保持捕获的 `RunOptions`。

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
- Multi-Agent 编排；
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
