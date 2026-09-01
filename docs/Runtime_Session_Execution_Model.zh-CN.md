# Runtime / Session 执行模型

[English](Runtime_Session_Execution_Model.md)

## 范围

本文定义 0.1.0 已实现的执行与生命周期规则。更高层组件图见[架构](InvestOrch_Agent_Architecture.zh-CN.md)。

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
- Clear 删除 idle、unarchived 且没有 queued intent 的 Session。
- Compact 用带标记的 assistant summary 替换 SDK continuation；Journal 保持完整。
- Fork 把来源 Session 最后稳定提交头部 clone 为新的独立 Session。

Fork 复制 SDK continuation、存在时的 Journal、带后缀 title 和直接 parent lineage。它不复制 active Run、defaults、Steer/Queue、Todo、approval、archive state、job、usage、cache 或 UI state。它是 stable-head cloning，不是 historical-turn branching。

从未获得 SDK message、Journal record、metadata、queued intent 或 lineage 的空 Session 会被丢弃。Web 启动时不创建 Session，直到用户明确创建；TUI/plain 会创建初始 Session。

## Output 与 replay

每个 semantic `OutputEvent` 都带 Run 与 Session 所有权。Application 先把它追加到来源 Session 的 JSONL Journal，再进行 live projection。

SDK SQLite Session 是 model-continuation state，可以被压缩。JSONL Journal 是追加式用户可见 replay state。未选中 Session 的 output 仍写 Journal。客户端按原始 Journal sequence 协调分页历史与 live events。

Activity label 是异步 derived annotation，指向 Tool-call sequence。缺少 label 时显示真实 Tool name，不改变执行事实。

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
