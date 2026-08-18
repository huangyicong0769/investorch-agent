# QMT Agent Trader — Tool Capability Roadmap

> 状态：Living Document
>
> 建立日期：2026-08-14
>
> 目的：记录 QMT Agent Trader 在进入多 Agent、数据池和交易系统之前，单 Agent 应具备的通用 Tool 能力、边界、优先级与验收标准，作为后续开发的长期基准。

---

## 1. 当前阶段的核心决策

现阶段优先级明确调整为：

```text
先完成单 Agent 的通用工具能力
        ↓
建立清晰的副作用 / 授权边界
        ↓
补足文件与结构化数据处理能力
        ↓
验证单 Agent 可以稳定完成复杂任务
        ↓
再评估 Multi-Agent
        ↓
最后再进入 Data Pool / Quant Data / Trading
```

当前**不优先**：

- Research Agent
- Planner Agent
- Multi-Agent routing
- Data Pool / 数据仓库
- Market Data 抽象
- QMT 交易接入
- 自动交易

原因：

1. 如果基础 Tool 能力不完整，过早引入 Multi-Agent 会把“能力缺失”和“协作设计”两个问题混在一起。
2. Data Pool 会显著增加数据模型、生命周期、缓存、更新、索引和一致性等心智负担，应在基础执行能力稳定后再设计。
3. 单 Agent 足够强、Tool 边界足够清楚之后，哪些职责值得拆成 Specialist Agent 会更自然地显现。

---

## 2. 与总架构文档的关系

本文档是 `QMT_Agent_Trader_Architecture.md` 的工具能力实施路线补充，不替代总架构文档。

继续遵守现有核心原则：

- Agent 是用户入口。
- Tool 是 Agent 可调用的能力边界。
- OpenAI Agents SDK 负责通用 Agent Runtime。
- DeepSeek Responses API 负责模型服务。
- 优先复用成熟第三方库和 MCP 能力。
- 不为第三方能力增加仅用于改名的 Wrapper / Adapter / Provider。
- 需求驱动抽象，不为未来可能出现的需求提前建立架构层。
- 普通错误使用异常表达，不创建额外 Error Policy / Validation Framework。
- 人类开发者必须能够理解并维护代码。

本文档进一步增加一个阶段性原则：

> **在 Tool 能力足够完整之前，不通过增加 Agent 数量来弥补能力缺口。**

---

## 3. 当前基线

截至 2026-08-14，项目已经具备以下基础能力。

### 3.1 已有 Runtime / Agent 能力

- OpenAI Agents SDK + DeepSeek Responses API
- SQLite Session
- Session 创建、恢复、清除、标题
- Title Agent
- Summary Agent
- Todo / Plan-Solve
- Streaming runtime observability
- reasoning / action / observation 展示
- 长 reasoning / tool output 的 Summary Agent 压缩
- MCP Server Manager

这些属于 Application Runtime / Agent Runtime，不应因为 Tool Roadmap 再包装一次。

### 3.2 已实现本地 Function Tools

```text
get_current_time
write_todos
calculate
explore
edit
delete
exec_command
```

### 3.3 已接入 MCP 外部能力

当前通过 MCP 使用 Tavily，可提供类似：

```text
search
extract
crawl
map
research
```

如果 MCP 已经提供合适的能力，不重新实现同名 Web Tool。

### 3.4 当前 Tool 占位模块

项目中已经存在但尚未进入真实实现阶段的领域模块：

```text
market.py
portfolio.py
quant.py
research.py
trading.py
```

这些文件的存在不意味着现在必须填满。

原则仍然是：

> 真实需求出现后再实现；不因为文件已经存在而提前制造 API。

### 当前状态（2026-08-18）

本轮 General Tools stabilization 已完成以下通用能力：

```text
calculate                         → completed
explore(list/read/search)         → completed
edit(create/append/replace)       → completed
delete                            → completed
exec_command(foreground/background) → completed
```

`exec_command` 是唯一的通用 shell 入口；任意 Python、Polars、NumPy、CLI
和结构化数据处理都通过它在 Workspace 中执行，不另建 `run_python` 或
`inspect_table/query_table/summarize_table` DSL Tool。

后续通用层以稳定性维护为主，下一阶段可进入真实的 Market / Portfolio /
Trading 需求。

---

## 4. Tool 的分类模型

当前阶段把 Agent 可用能力分为六类。

```text
1. Runtime Utility
2. External Information / MCP
3. Workspace / Filesystem
4. Structured Data
5. Computation / Data Processing
6. Domain Tools（未来）
```

同时，每个 Tool 必须额外具有一个副作用属性：

```text
Read-only
Side-effect
```

这两个维度彼此独立。

例如：

```text
read_text_file      = Workspace + Read-only
write_text_file     = Workspace + Side-effect
get_portfolio       = Domain + Read-only
place_order         = Domain + Side-effect
```

这个分类只用于理解和授权边界，第一版不需要建立 `ToolRegistry`、`ToolCategory` 类或复杂 metadata framework。

---

## 5. Phase A — Runtime Utility Tools

目标：补足 Agent 在所有任务中都会复用、且不依赖量化领域的数据能力。

### 5.1 已完成

#### `get_current_time`

用途：

- 获取当前日期时间
- 明确时区
- 为最新数据查询、报告日期、任务判断提供确定性时间来源

#### `write_todos`

用途：

- 当前单次用户任务的 Plan-Solve 状态
- 规划、进度更新、重新规划

约束：

- Todo 属于当前 `Runner.run` 的 execution state。
- 不作为长期 Session 数据存储。
- 不为了 todo 创建独立持久化系统。

### 5.2 `calculate`（已完成）

需要一个确定性的通用计算能力，但 API 应保持粗粒度。

当前 Tool API：

```text
calculate(expression)
```

或 SDK / MCP 已有等价能力时直接复用。

当前实现提供受限、确定性的算术表达式求值，并带有表达式、AST、整数位数
和指数边界校验。

适合：

- 普通算术
- 百分比
- 比率
- 简单财务计算
- Agent 不应依赖语言模型心算准确性的场景

不建议拆成：

```text
add()
subtract()
mean()
median()
percentage_change()
```

除非后续某类计算真正形成独立业务语义。

### 5.3 日期计算

只有在出现明确需求时，再考虑补充日期运算能力，例如：

```text
日期差
交易日前后推算
时间区间解析
```

优先直接使用 Python 标准库 / 成熟库实现，不创建 DateService 框架。

---

## 6. Phase B — Workspace / Filesystem Tools

这是当前已经完成并持续稳定的通用能力。

目标：让 Agent 从“聊天 + 搜索”升级为能够读取、组织和产出用户本地工作材料的执行器。

### 6.1 Workspace 边界

Agent 不应默认获得整台机器的文件系统权限。

应存在一个明确的 Workspace Root：

```text
Workspace Root
    |
    +-- user files
    +-- reports
    +-- data
    +-- temporary outputs
```

所有文件 Tool 的路径必须限制在 Workspace Root 内。

需要防止：

```text
../ path traversal
absolute path escape
symlink escape（如实际实现涉及）
```

第一版只需要可靠限制访问范围，不需要设计通用 sandbox framework。

### 6.2 Read-only 文件能力

当前由单一 `explore` Tool 提供：

```text
explore(operation="list" | "read" | "search")
```

#### `list_directory`

职责：

- 查看目录中的文件 / 子目录
- 返回名称、类型和必要的基础 metadata

不承担：

- 内容理解
- 自动递归整个 Workspace
- 建索引

#### `read_text_file`

职责：

- 读取 UTF-8 文本内容
- 支持 `.txt` / `.md` / 常见配置文本
- 对超大文件提供合理的大小 / 行数控制

大输出仍由现有 observability Summary Agent 负责“人类展示压缩”；不要因为 CLI 显示问题改变 Tool 实际返回给 Main Agent 的内容语义。

#### `search_files`

职责：

- 按文件名查找
- 必要时按文本内容搜索

第一版可基于成熟文件搜索方式实现。

不要提前创建：

```text
FileIndex
WorkspaceSearchEngine
EmbeddingIndex
VectorStore
```

### 6.3 Side-effect 文件能力

当前由 `edit` / `delete` Tool 提供：

```text
edit(create | append | replace)
delete
```

#### `edit`

职责：

- 新建、追加或精确替换 UTF-8 文本文件
- 保存 Agent 生成的文本结果
- 通过审批流程执行副作用操作

#### `delete`

职责：

- 删除 Workspace 内文件

必须视为明显 Side-effect。

### 6.4 第一版不做的文件能力

旧的独立文件 Tool 计划已被当前实现取代；不再新增平行 wrapper。仍暂不实现：

- 全磁盘搜索
- 文件系统 watcher
- 自动 embedding / indexing
- Git 操作 Tool
- 远程 SSH / SFTP

---

## 7. Phase C — Structured File / Data Tools

目标：让 Agent 能够处理研究、回测和后续量化工作中常见的结构化文件，而不立刻进入 Data Pool。

当前不预建专用 Structured Data DSL。需要处理 JSON、TOML、CSV、TSV 或表格
时，优先由 `exec_command` 调用 Python 标准库、Polars、Pandas 或其他成熟
CLI / Python 生态；真实业务需求出现后再设计有独立语义的 Tool。

推荐顺序：

```text
JSON / TOML
    ↓
CSV / TSV
    ↓
XLSX
    ↓
PDF（单独阶段）
```

### 7.1 JSON / TOML

优先使用普通 Python 库解析。

能力可以按实际需求表现为：

```text
read_json
write_json
read_toml
```

但如果 `read_text_file` + Agent 已经足够处理小型配置文件，不必为了格式完整性强行创建独立 Tool。

判断标准：

> 独立 Tool 是否真的提高可靠性、结构化输出或减少模型负担？

### 7.2 CSV / TSV

这是结构化数据能力的第一重点。

Agent 应能够：

- 查看 schema / columns
- 查看行数
- 读取指定范围
- 过滤
- 排序
- 聚合
- 选择列
- 基础统计

不要把整张大型 CSV 永远一次性塞入 LLM context。

应该尽可能让确定性数据处理发生在 Python / Polars / Pandas 中。

推荐优先考虑成熟库：

```text
Polars
Pandas
```

具体选型以实现简单和现有需求为准。

### 7.3 XLSX

XLSX 在 CSV 稳定后再进入。

需要支持的核心能力：

- sheet 列表
- 表格区域读取
- 结构化数据提取
- 新建 / 修改 workbook
- 保存结果

不要在第一版实现完整 Excel 自动化平台。

### 7.4 PDF

PDF 不视为“普通文本格式”。

原因：

- 页面布局
- 表格
- 图片
- 图表
- 扫描件
- OCR
- 多栏文本

都会让读取和理解复杂化。

因此 PDF 应单独设计与验证，而不是作为 `read_text_file` 的一个扩展名。

进入 PDF 阶段时优先复用成熟 PDF 能力，而不是自研解析器。

---

## 8. Phase D — Computation / Data Processing

目标：把适合确定性程序处理的工作留给程序，而不是让 LLM 手工算。

### 8.1 基础原则

不把每个 Python 函数暴露成 Tool。

例如不推荐：

```text
numpy_mean
numpy_std
pandas_sort
polars_filter
```

Tool 应表达 Agent 真正需要的能力，而库函数只是 Tool 内部实现。

### 8.2 第一阶段能力

通用计算（已完成）：

```text
calculate
```

结构化数据操作不预建概念 API；通过 `exec_command` 使用成熟库，避免提前
锁定 `inspect_table/query_table/summarize_table`。

### 8.3 表格数据处理建议

一个好的结构化数据 Tool 应尽可能返回：

- 操作结果
- 行 / 列数量
- 使用的筛选条件
- 必要的统计结果

避免把完整大型 DataFrame 序列化给模型。

### 8.4 Python 执行

临时数据分析、快速研究验证、图表计算和数学建模统一通过已审批的
`exec_command` 执行。`run_python(arbitrary_code)` 不是独立 Tool；不要为
它再建一层 wrapper。

---

## 9. Phase E — Output / Artifact Capabilities

目标：让 Agent 不只返回聊天文本，还能把工作结果稳定保存为用户可继续使用的文件。

建议演进顺序：

```text
Markdown / Text
    ↓
JSON / CSV
    ↓
XLSX
    ↓
Charts
    ↓
PDF / Report
```

### 9.1 第一阶段

依靠：

```text
write_text_file
```

即可覆盖：

- Markdown 报告
- 研究笔记
- 配置草稿
- handoff 文档

先不要增加：

```text
ReportManager
ArtifactService
ExportFramework
```

### 9.2 何时增加语义化 Output Tool

只有当某类输出已经形成稳定工作流时，才考虑类似：

```text
save_research_report
export_backtest_result
```

底层仍可复用普通文件 I/O。

---

## 10. External Information / Web / MCP

当前 Web 能力优先继续通过 Tavily MCP 使用。

原则：

> MCP 已经提供合适能力时，不在本地再实现同一层 API。

现阶段更值得验证的是：

- timeout 行为
- retry 行为
- tool error 是否清楚返回给 Agent
- 并行 tool calls
- observability
- 长输出 Summary
- source / URL 保留

而不是建立：

```text
WebSearchService
ResearchProvider
TavilyAdapter
SearchBackend abstraction
```

除非未来确实出现多个搜索后端并需要统一业务语义。

---

## 11. Authorization / Side-effect Boundary

这是进入真实文件写入和未来交易之前必须逐步建立的能力。

### 11.1 第一版原则

Tool 分成：

```text
Read-only
Side-effect
```

Read-only 默认允许执行。

Side-effect 根据风险决定是否需要用户批准。

### 11.2 通用工具示例

Read-only：

```text
get_current_time
read_text_file
list_directory
search_files
inspect_table
```

Side-effect：

```text
write_text_file
move_file
delete_file
```

### 11.3 未来交易工具自然复用同一心智模型

Read-only：

```text
get_market_snapshot
get_price_history
get_portfolio
get_orders
```

Side-effect：

```text
place_order
cancel_order
```

因此现在建立 Side-effect 边界，不只是为了文件系统，而是在为交易系统验证授权模型。

### 11.4 暂不增加自动授权判断 Agent

当前阶段不让 Agent 自己判断“需不需要授权”。

授权策略应该由应用 / Tool 配置确定。

后续真实需求出现时，再考虑更复杂的 approval policy。

---

## 12. 哪些东西不是 Tool

为了避免 Main Agent 的 Tool 列表不断污染，需要长期坚持这条边界。

以下属于 Runtime / Application Infrastructure，不应该默认暴露给 Main Agent：

```text
Session 创建 / 恢复 / 删除
Session Title
Title Agent
Summary Agent
MCP 生命周期管理
observability renderer
tracing
config loading
SQLite session metadata
Agent construction
Runner configuration
```

判断标准：

> 用户是否会自然地要求 Agent “使用这个能力完成任务”？

如果只是应用运行自身所需，则通常不是 Tool。

---

## 13. Domain Tools — 暂缓但保留方向

通用能力稳定后，才进入量化交易领域 Tool。

未来大类仍然包括：

```text
Market
Portfolio
Research
Quant
Backtest
Trading
```

可能的业务 Tool：

```text
get_market_snapshot
get_price_history
get_portfolio
get_orders
analyze_technicals
run_backtest
place_order
cancel_order
```

这些 API 在真正实现 QMT / 数据能力时再确定。

不要因为当前存在空的 `market.py` / `portfolio.py` / `quant.py` / `research.py` / `trading.py` 就提前锁定设计。

---

## 14. Data Pool — 明确暂缓

Data Pool 当前不进入实现阶段。

它未来可能需要解决：

- 数据来源
- schema
- symbol normalization
- frequency
- trading calendar
- corporate actions
- cache
- incremental update
- freshness
- missing data
- revision
- storage format
- query interface
- derived features
- provenance

这些问题彼此耦合，心智负担明显高于当前通用工具开发。

在以下条件满足前不设计 Data Pool：

1. Workspace / filesystem 稳定。
2. CSV / structured data 处理稳定。
3. 基础计算工具稳定。
4. 已经出现至少一个真实 Market / Research 数据需求，能够反向驱动数据模型。

原则：

> 先让 Agent 会处理数据，再设计长期保存哪些数据。

---

## 15. Multi-Agent — 明确暂缓

现阶段不通过增加 Agent 数量扩充能力。

Multi-Agent 的进入条件建议为：

1. Main Agent 已拥有足够的通用 Tool 能力。
2. 单 Agent Plan-Solve 可以稳定完成多步骤任务。
3. observability 能明确看到 Tool 使用和任务进度。
4. 出现某类工作具有稳定、独立、可复用的职责边界。
5. 将该职责拆成 Specialist Agent 能减少 Main Agent 复杂度，而不是仅仅增加调用层级。

可能的未来 Specialist：

```text
Research Agent
Fundamental Agent
Market Data Agent
Portfolio Agent
Trading Agent
```

但这些只是候选，不是当前承诺。

判断标准：

> 先证明职责独立，再创建 Agent。

---

## 16. 推荐实施顺序

当前 Roadmap：

### Stage 0 — 已完成 / 正在稳定

```text
[✓] get_current_time
[✓] write_todos
[✓] calculate
[✓] explore(list/read/search)
[✓] edit(create/append/replace)
[✓] delete
[✓] exec_command(foreground/background)
[✓] Plan-Solve prompt
[✓] runtime observability
[✓] reasoning/action/observation
[✓] Summary Agent for long traces
[✓] Tavily MCP
```

### Stage 1 — 通用 Workspace（已完成）

```text
[✓] explore(list/read/search)
[✓] Workspace Root path boundary
```

验收：

- Agent 能查看 Workspace。
- Agent 能读取用户指定文本文件。
- Agent 能查找文件。
- Agent 无法逃逸 Workspace Root。
- 所有行为可观察。

### Stage 2 — 文件 Side-effect + Approval（已完成）

```text
[✓] edit(create/append/replace)
[✓] delete
[✓] side-effect approval path
```

验收：

- Read-only 不需要确认。
- 高风险写操作能够触发用户确认。
- Agent 无法写到 Workspace 外。
- error / rejection 能正常回到 Agent。

### Stage 3 — Structured Data（按需，不预建 DSL）

```text
[✓] exec_command + Python ecosystem
[ ] dedicated structured-data Tool after a real domain requirement
```

验收：

- Agent 不需要把大型 CSV 全量塞入上下文。
- 确定性筛选 / 聚合由代码完成。
- 结果可被 Agent 继续推理。

### Stage 4 — Generic Calculation（已完成）

```text
[✓] calculate
[ ] date / interval calculation when needed
[ ] structured statistical summaries
```

验收：

- 模型不依赖心算完成重要数值计算。
- 不为每个数学函数制造 Tool。

### Stage 5 — Rich Files / Artifacts

```text
[ ] XLSX
[ ] Charts
[ ] PDF
[ ] richer report export
```

这一阶段继续优先复用成熟库 / capability。

### Stage 6 — Re-evaluate Architecture

完成前面阶段后，再重新评估：

```text
Multi-Agent?
Data Pool?
Market Tools?
QMT integration?
Backtest?
Trading?
```

不要预先假定答案一定是“全部都做”。

---

## 17. 建议的代码演进方式

当前不要为 Roadmap 一次性创建所有目录和空文件。

实现 Workspace 时再增加相应模块，例如：

```text
src/qmt_agent/
├── tools/
│   ├── time.py
│   ├── todo.py
│   └── filesystem.py
```

如果 filesystem 内部实现开始变复杂，再考虑普通 Python 模块：

```text
src/qmt_agent/
├── workspace.py
└── tools/
    └── filesystem.py
```

依赖方向：

```text
Agent
  ↓
Function Tool
  ↓
ordinary Python implementation / mature library
```

不要反向依赖：

```text
workspace implementation -> Agent
```

不要提前建立：

```text
ToolManager
ToolRegistry
ToolFactory
FilesystemServiceInterface
StorageProvider
WorkspaceProvider
CapabilityRouter
```

只有真实重复和复杂度出现后才抽象。

---

## 18. 每个新 Tool 的 Definition of Done

以后新增 Tool 时，至少检查以下问题。

### 18.1 能力边界

- 这个能力真的需要由 Agent 调用吗？
- 它是否表达一个清楚的用户 / 业务能力？
- 是否只是给已有库函数换名字？

### 18.2 Schema

- 参数是否足够明确？
- 类型是否能被 Agents SDK / Pydantic 正确生成 schema？
- Tool description 是否让模型知道何时调用？
- 是否避免不必要的参数和模式枚举？

### 18.3 错误

- 非法输入是否明确失败？
- 外部依赖超时是否能返回清楚错误？
- 不创建额外错误框架。

### 18.4 Side-effect

- 是 Read-only 还是 Side-effect？
- Side-effect 是否需要用户批准？
- 拒绝以后是否能正常返回 Agent？

### 18.5 Observability

必须能够看到：

```text
Agent reasoning
Tool name
Tool arguments
Tool output / summary
Tool error
```

长输出允许 Summary Agent 压缩人类展示，但不能让显示层偷偷改变 Main Agent 实际得到的结果。

### 18.6 测试

至少覆盖：

- 正常路径
- 典型非法输入
- 权限 / path boundary（如适用）
- 副作用行为（如适用）
- Tool schema 可以成功创建

---

## 19. 设计检查清单

当想增加一个新模块、类、Agent 或 Tool 时，依次问：

```text
1. 现在真的有这个需求吗？
2. 成熟库 / MCP 已经提供了吗？
3. 普通 Python 函数能解决吗？
4. 它需要暴露给 Agent 吗？
5. 它有独立的业务语义吗？
6. 是否只是为了未来兼容而增加一层？
7. 是否会增加人类开发者的理解成本？
8. 如果现在不抽象，会产生什么真实问题？
```

如果第 8 个问题没有清楚答案，通常先不抽象。

---

## 20. 当前下一步

本轮 General Tools stabilization 已完成。按照本文档，下一项推荐开发任务是：

```text
Market / Portfolio / Trading 的第一个真实业务需求
```

通用 Tool 层进入冻结和维护状态；结构化数据仍优先通过
`exec_command` + 成熟 Python 生态完成，不提前设计 DSL、Data Pool 或
Multi-Agent。

---

## 21. 本文档维护规则

本文档是 Living Document。

当以下情况发生时应更新：

- 某个 Stage 完成。
- Tool API 被真实实现并稳定下来。
- 某项暂缓能力开始进入开发。
- 实际需求推翻了本文档中的假设。
- 多 Agent / Data Pool 的进入条件已经满足。

更新时应记录“现在真实发生了什么”，不要把未来设想伪装成已经确定的架构。

核心长期原则保持不变：

> **先完成能力，再拆分角色。**
>
> **先处理真实数据，再设计数据池。**
>
> **需求先出现，抽象后出现。**
