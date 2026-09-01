# InvestOrch Agent 架构

[English](InvestOrch_Agent_Architecture.md)

## 范围

本文描述 0.1.0 已实现的架构。未来产品方向记录在[产品路线图](InvestOrch_Agent_Product_Roadmap.zh-CN.md)。

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
- Bootstrap Agent 在 `investorch --sync` 时合并项目模板。

Runtime 使用 OpenAI Responses model adapter。随包配置当前把所有角色指向 DeepSeek。Model name、base URL、secret name 和 reasoning effort 来自 `AppConfig`。

## 当前 Tool Surface

Main Agent 当前获得：

- Workspace 与执行：`explore`、`edit`、`delete`、`exec_command`；
- Utility 与状态：`calculate`、`get_current_time`、`write_todos`；
- 配置：`get_config`、`update_config`；
- MCP registry：`list_mcp_servers`、`configure_mcp_server`、`remove_mcp_server`；
- 回测：`run_backtest`，以及选择原生 bundle 时的 `inspect_rqalpha_data`。

Tool 直接使用 Agents SDK Tool definitions。0.1.0 没有本地 Tool framework、registry abstraction、Market Tool、Portfolio Tool、Trading Tool 或 QMT 模块。

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

- `<root>/investorch.toml`：本地 overrides 与 secrets；
- `<root>/mcp.toml`：MCP registry；
- `<root>/workspace/`：用户拥有的 Workspace 与生成 artifacts；
- `<state>/sessions.db`：Agents SDK continuation 与 Application Session metadata；
- `<state>/sessions/<session-id>.jsonl`：追加式用户可见 Journal；
- `<state>/logs/investorch.log`：轮转诊断日志。

SQLite continuation 是 model state，可以被压缩；JSONL Journal 是 replay state，不被压缩。Activity label 是 derived annotation，不是执行事实。

Bootstrap template 只在 target 不存在时复制。`--sync` 使用配置的 Bootstrap Agent，把当前项目规则合并到用户已有文件中，同时保留长期内容。`--sync-force` 跳过模型，以随包模板原子替换目标文件。两种模式都会验证每个已修改的目标；单个文件处理失败时恢复该文件，并将被替换的内容备份到 `<state>/bootstrap-backups/<timestamp>/`。

## 配置

`AppConfig` 验证随包 TOML defaults 与本地 overrides。部分设置对未来 Run 热更新；会改变 composition 的设置报告需要重启。Agent-facing 读取会隐藏 secrets，Agent-facing 写入不能修改 secrets。

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

0.1.0 尚未实现：

- 组合、账户、订单、持仓监控或实盘交易能力；
- QMT Gateway 或直接 XtQuant 集成；
- 统一投资数据层；
- Multi-Agent 编排；
- 跨进程重启持久化 active Run、pending approval、Steer、Queue 或 Todo；
- 历史 turn conversation branching；
- per-Session Workspace 或跨 Run filesystem locking；
- 经过认证的 LAN 或 remote Web serving。
