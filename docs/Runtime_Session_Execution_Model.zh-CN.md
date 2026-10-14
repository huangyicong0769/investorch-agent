# Runtime / Session 执行模型

[English](Runtime_Session_Execution_Model.md)

## 范围

本文定义执行、生命周期、Session 访问与 Agent 消息提交规则。更高层组件图见[架构](InvestOrch_Agent_Architecture.zh-CN.md)。

## Identity 与所有权

InvestOrch 分离：

- **Session**：持久化对话 identity。
- **Run**：一次瞬时顶层 Agent turn。
- **Selection**：客户端当前显示的 Session。

改变 Selection 永远不会切换、取消或修改 active Run。

```text
Application Host
├── AppState
│   ├── selected_session_id       # TUI/plain selection
│   └── future-run defaults
├── AgentRuntime
│   ├── Session A -> ActiveRun A1
│   ├── Session B -> idle
│   └── Session C -> ActiveRun C1
└── ExecutionState
    ├── shared workspace sandbox
    └── global managed jobs
```

Web 客户端发送明确 Session ID，不依赖 Host 选中的 Session。`AppState` 保存 ID 和 defaults，不持有打开的 SDK Session handle。每个 Run 打开并关闭自己的临时 `SQLiteSession`。

## Application boundary

普通输入以明确 Session ID 进入 `application.interaction.submit_user_input()`。它决定启动 Run 还是提交 Steer/Queue，并返回包含稳定所有权 ID 的 acknowledgement。

Session mutation 进入 `application.sessions.SessionOperations`。它通过 Runtime maintenance reservation 协调 SDK continuation、metadata 和 Journal，覆盖 create、unused cleanup、title、archive、unarchive、fork、clear 与 compact。Presentation client 负责 command parsing、selection 和用户提示。

Agent 的 `list_sessions`、`read_session` 委托 `application.session_queries` 中的无状态函数。Host 将列表查询绑定到自己的数据库与当前 Runtime，将历史查询绑定到自己的数据库与已有 SessionJournal；在 Runtime 构造后、yield 前一次注册已绑定的 Tools。AgentContext 与 AgentLoop 不携带 Session 读取能力或 Provider。列表为每个目标 Session 调用 Runtime public snapshot，历史查询不需要 Runtime，Tool 仅转发公开参数。列表状态概括当前进程：active Run 的 phase、无 active Run 但有已接受 Queue/Steer 工作时的 `pending`，或 `idle`；不暴露队列内部状态或历史 Run 结果。精确 ID 读取只提供历史，包括自身、运行中与归档 Session，不改变 Selection、不创建 Run、不申请 maintenance reservation。Tool 参数、结果预算和分页示例见 [Session 访问](InvestOrch_Agent_Architecture.zh-CN.md#session-访问030-stage-a)。

`presentation.py` 把 Application value 转换为明确的 JSON-safe projection dictionaries。

## Run 生命周期

`AgentRuntime.start_run()` 同步执行：

1. 拒绝同一 Session 的第二个顶层 Run；
2. 创建唯一 Run ID；
3. 捕获不可变 `RunOptions`；
4. 注册并启动 `asyncio.Task`。

Task 随后打开 SDK Session、按需记录输入、调用可重入 `AgentLoop`，并在 `finally` 中关闭 SDK Session 和 Runtime registry entries。

不同 Session 可以并发。Manual compaction、clear、fork、archive 和顶层 Run 仅对同一 Session 通过 maintenance reservation 互斥。

## Future defaults 与 Run snapshot

Reasoning effort、permission mode 和 follow-up behavior 是未来 Run 的 defaults。运行中的 `ActiveRun` 拥有不可变 snapshot。改变 default 不会修改 in-flight behavior。

Queue 或 Steer follow-up 在提交时捕获将来适用的 defaults，后续变化不会改写已捕获 options。

## Follow-up

### Steer

Steer 仍属于同一个顶层 Run：

1. 以 FIFO 顺序 reserve input；
2. 向 Session Journal 追加 `user_steer`；
3. 请求当前 streamed turn 在安全 boundary 停止；
4. 审批处理完成后，用同一 Run ID 从 SDK state 继续。

如果 SDK result 已经 terminal，Steer 会提升为后续 Run，不重复写 Journal。

### Queue

Queue 是 SDK continuation 外部的 Application-level per-Session FIFO。提交只保存意图，尚不创建 conversation event。Run 成功完成后，最早的 item 只以 `user_message` 写入一次 Journal，并提升为新 Run。

Run 停止或失败会暂停剩余 Queue。Resume 提升一个 head item；Clear 丢弃尚未提升的意图，不改变 conversation history。

## Agent 消息提交

发送方审批通过后，`send_message` 复用 `submit_user_input` 路由。Idle 目标启动 Run；active 目标由其冻结的 `RunOptions` 选择 Steer 或 Queue，future defaults 用于推广后的 Run。发送方在接受后获得 `UserInputSubmission`，不等待目标执行结果：

| Disposition | 接受边界 | 持久化 |
| --- | --- | --- |
| `run_started` | Runtime 安排新的顶层 Run | 初始 `user_message` 异步写入；回执不等于 Journal 提交 |
| `steer_submitted` | 已有 journal-first Steer 提交流程完成 | Steer 已记录，消费仍等待安全边界 |
| `queue_submitted` | Runtime 接受进程内 FIFO 输入 | promotion 前没有消息历史；不支持 crash/restart 恢复 |

发送 Session/Run ID 随不可变 `UserInput.origin` 传递，与目标 Run ID 分开。初始输入、Steer continuation、Queue promotion 和 terminal Steer fallback 使用同一 SDK 转换与 Journal 事件类型。有效 Agent 指示参加同一冻结 Review 历史。通用 `user_input_committed` 视图通知只在 `user_message` 可读后发出；history/live 根据 Journal seq 去重。

自投、缺失、归档、stopping、被保留的目标或未解决的 pending work 按已有 lifecycle 规则失败。不增加重试队列、自动回复、完成回调或持久 Mailbox。发送方在待审时取消不影响目标；投递接受后，输入与 Run 由目标持有。Fork 随历史复制可选 origin，Compact 仍不改 Journal。

## Stop 与取消

停止 Run 会把 phase 设为 `stopping`，关闭 follow-up submission，暂停保留的 Queue intent，并取消 Run task。清理走正常 `finally` 路径。Pending manual approval 被取消，不伪造 approve/reject decision。

受管后台 job 在来源 Run 结束或取消后仍由 Application 持有并继续运行。

## Todo 状态

Todo 是投影到客户端的瞬时 Run state，不写入 SDK continuation、JSONL Journal、Session metadata 或 fork。新 Run 从空 Todo snapshot 开始。

## Session 生命周期

- Create 创建新的持久化 identity。
- Rename 更新 Session metadata。
- Archive 保留 continuation、Journal、title 和 lineage，同时使 Session 在恢复前只读。
- Unarchive 恢复普通操作。
- Delete 删除显式 Session 及其全部递归 Spawn 后代；Clear 使用同一删除契约，再创建不继承来源关系的普通新 Session。
- Compact 用带标记的 assistant summary 替换 SDK continuation；Journal 保持完整。
- Fork 把来源 Session 最后稳定提交头部 clone 为新的独立 Session。

Fork 复制 SDK continuation、存在时的 Journal、带后缀 title 和直接 parent lineage。它不复制 active Run、defaults、Steer/Queue、Todo、approval、archive state、job、usage、cache 或 UI state。它是 stable-head cloning，不是 historical-turn branching。

从未获得 SDK message、Journal record、metadata、queued intent 或 lineage 的空 Session 会被丢弃。Web 启动时不创建 Session，直到用户明确创建；TUI/plain 会创建初始 Session。

## Output 与 replay

每个 semantic `OutputEvent` 都带 Run 与 Session 所有权。Application 先把它追加到来源 Session 的 JSONL Journal，再进行 live projection。

SDK SQLite Session 是 model-continuation state，可以被压缩。JSONL Journal 是追加式用户可见 replay state。未选中 Session 的 output 仍写 Journal。客户端按原始 Journal sequence 协调分页历史与 live events。

Activity label 是异步 derived annotation，指向 Tool-call sequence。缺少 label 时显示真实 Tool name，不改变执行事实。

Agent Conversation 历史使用独立扁平投影：已接受用户消息、证据确认激活的 Steer（普通 user 记录）、Assistant 消息和 Tool call/result。Reasoning、approval 和 pending/discarded Steer 不进入此视图；可选 raw Events 在响应预算内保留原字段。Conversation 图片仅含元数据。分页冻结已提交 `snapshot_seq`，用排他的 `next_before_seq` 继续；处置证据检查到整个 snapshot，包括位于页面 cursor 之后的证据。Journal snapshot 捕获短暂持锁，后续扫描排除新追加。空历史使用 `snapshot_seq=0`。Web/TUI replay 和 Review 严格授权策略保留各自原有接口。

## 审批

每个 `ApprovalRequest` 都带不可变 approval ID、Run ID、Session ID 和已捕获 permission mode。Resolution 只作用于该 request。

- `manual`：始终询问用户。
- `review`：Permission Agent 可以 approve、reject 或 ask；失败和无效输出回退到询问。

Approval decision 写入来源 Session Journal。多个 Session 可以同时等待审批。

## Client projection

Web 与 TUI 是同一 Application state 的一级 projection。

- TUI 可以在其他 Run 继续时切换 Session，并组合完整 Journal replay 与 live state。
- Web 使用 REST 获取 snapshot 和执行 mutation，用 paged history replay，并以 WebSocket events 接收 live changes。
- 两者根据各自 UI 设计呈现 Session state、Run status、Steer/Queue、approval、archive/fork/compact、managed jobs 和 usage。
- plain console 在读取下一条输入前等待一个 Run 完成，因此不提供 live cross-Session interaction。

客户端都不拥有第二份执行事实。

## Usage 与 compaction

Main 与 auxiliary model usage 在当前进程中按 Session 累积。Auxiliary usage 包含 Title、Activity、Permission 和 Compact Agents。显示的 Main context occupancy 使用最后一次 physical Main request，而不是累计 Session totals。

Manual 或 automatic compaction 只修改 SDK continuation。已完成答案不会因后续压缩失败而丢失；replacement 出错时会尝试恢复原 continuation snapshot。

## 后台 Job

`ExecutionState` 全局拥有受管 job。每个 job 保存 owner Session 和 Run ID 以便归属。Job listing 是 Application 全局的，job 可以比来源 Run 存活更久。

## Shutdown

存在 active Run 或 queued follow-up 时，普通客户端退出会被阻止。Defensive shutdown 会取消剩余 Run task、等待正常清理、关闭共享 execution environment 和 MCP manager，并丢弃未持久化 transient queues。

## 当前限制

0.1.0 不跨进程重启持久化 active Run、pending Steer、Queue、Todo、pending approval、usage display 或 UI state。所有 Session 共享同一个 Workspace 和 managed-job registry。当前没有 historical-turn branching、per-Session Workspace、distributed scheduler 或 cross-process Runtime recovery。

## 图片输入与回放

Run、Steer、Queue 使用同一 `UserInput`；Portfolio Ask 的 developer 上下文与用户输入分开。Queue 晋升后完整记录输入；Steer 处置记录引用原序号，不重复图片。旧文本调用在公共入口转换，模型用户输入统一为 structured content。仅含应用 developer 指令的 Run 没有用户 payload。

Journal 在用户消息、Steer、Assistant、Tool 输出增加可选 `images`；旧历史缺失该字段即无图片，无需迁移。Fork 复制内联 SDK/Journal 内容，归档保留内容。Delete/Clear 删除旧范围的历史；Clear 再创建空的替代 Session。Web 图片草稿按 Session 保存，乐观消息在持久事件到达前仍保留图片。

## Session 派生与全局并发

`spawn_agent(task, title?)` 使用已有 Permission/HITL 审批和普通 `submit_user_input`。子 Session 使用相同 Main 模型、Tools、Skills 和独立审批规则，拥有各自 continuation 与 Journal。初始输入保持 `user` role，来源由 SDK Session/Run/call 上下文提供。创建事务保存独立 Spawn lineage，并一次复制关联 Portfolio IDs；不复制父历史、Portfolio/Ledger 事实或授权。Fork 不继承 Spawn lineage；父完成、Stop 或 Archive 不级联子 Session。

工具调用身份生成稳定 child ID。已有 workflow 接受标记、Runtime 和 Journal 事实防止初始任务重复投递。确定因容量/关闭拒绝时只回收未启动 child；接受状态不确定时保留身份供诊断。回执只确认提交，不证明完成或重启恢复。递归子 Session 使用相同 Tools；不新增 stop/wait/status Tool、完成 registry 或自动回复。通过 `list_sessions` 观察当前状态，通过 `read_session(view="events")` 读取完成事实。

所有新顶层 Run 共享 `AgentRuntime._start_run()` 的同步准入门槛。`runtime.max_concurrent_runs` 默认 3，仅接受严格正整数，可在 Settings 或本地 TOML 热应用。下调不停止现有 Run，仅拒绝新准入并报告 `capacity_exceeded`（Web HTTP 429）。父 Run、普通输入、消息、Spawn 和 follow-up 晋升公平共用名额；Waiting Approval 与 Stopping 直到摘除仍占位。Title/Permission/Activity/Compact 辅助调用不额外占位。容量策略不属于冻结的 `RunOptions`。

容量拒绝会在 journal 前恢复并暂停原 Queue 队首，保留捕获的 options；显式 Resume 报容量错误并保留暂停队列。已持久接受的 terminal Steer fallback 保留原 seq，在其他 Run 或 reservation 结束时沿既有晋升路径重试。晋升首次 await 前已同步登记 Run，因此不会重复消费。拒绝不会把已完成 Run 改成 failed，也不新增全局队列或定时器。

显式 Delete/Clear 先预览所有名称与阻塞条件。多 Session Clear 必须展示完整范围和不可撤销提示：Web/TUI 使用确认弹窗，Plain 仅接受 `DELETE`，其他输入或 EOF 取消。取消/Escape 保留身份、历史和选择；等待确认时不持有 lifecycle lock。提交时锁住 Application 生命周期修改，并按稳定顺序取得完整 Spawn 范围的 reservation。任何活动、等待审批、停止中、maintenance 或 pending-input 节点都阻断整次操作，不自动 Stop。集合外独立 Fork 引用也阻断删除。各客户端回传显示的 Session ID 集合；后代变化要求重新确认。未显式提供范围的 Clear 调用只批准目标身份，防止意外扩大级联范围。通用 unused 清理不会删除 Spawn 节点；Archive/unarchive 保持独立。

一笔受保护的 SQLite 事务删除范围内 continuation、关联和 lineage，提交前失败会取消全部 Journal fence，保留所有身份和历史。提交后 JSONL/presentation 清理报告真实 deleted IDs；失败返回 `session_cleanup_failed` 和 `consistency_uncertain=true`，显式清理重试只接受已不存在的身份。若清理成功但 Clear 无法创建普通 replacement，`session_clear_replacement_failed` 如实报告全部 `deleted_session_ids`、`replacement_session_id:null` 和 `stage:replacement_creation`。两类后提交失败均不能回滚数据库。客户端移除已删除缓存与选择，禁止向旧对话输入，并提供显式恢复入口：TUI `Ctrl+N`、Plain `/new`、Web New Session 或 Retry cleanup。取消会等待 replacement 创建实际结束；已提交删除与新建日志支持断线后的事实重同步。Portfolio、Workspace、Skill 与外部产物不属于删除范围。

Web 将整行 Portfolio 横幅替换为右上紧凑卡片，分组展示关联 Portfolio、直接 child 与独立 Parent/Fork 导航；窄屏使用可访问的 Context 弹窗。当前 Runtime phase 优先；Completed/Failed/Cancelled 必须有可信最近 `run_ended` 且没有更新的 turn 活动，Idle 本身只表示 Idle。Sidebar 仍平铺，child 打开普通完整对话。TUI 在已有 Session metadata 显示 parent 短 ID。

普通 WebSocket Runtime 更新只修改已有 Context 查询缓存，不重复读取 Journal。空 Runtime 快照保留已有可信终态；新输入先清除旧终态，显示 Pending，随后由当前 Runtime/Journal 事实更新。输入提交和 Run 结束只刷新受影响的自身与 parent Context；新 child metadata 尚未可见时刷新关系，重连统一重同步。首次或较慢的 HTTP 读取会合并请求期间收到的 Runtime 快照，请求结束即丢弃临时监听器。历史终态仍来自 Journal，没有第二份完成状态存储。
