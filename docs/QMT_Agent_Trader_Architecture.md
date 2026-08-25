# QMT Agent Trader 项目架构 v0.2

> 本文档取代此前的 `QMT_Agent_Trader_Architecture_v0.1.md` 与 `QMT_Agent_Module_API_Outline_v0.1.md`。
>
> v0.2 的核心变化：
>
> - 不再自研 Agent Runtime / AgentRunner。
> - 使用 OpenAI Agents SDK 提供通用 Agent Runtime 能力。
> - 使用 DeepSeek Responses API 作为模型服务。
> - Tool 仍然是 Agent 与量化交易系统之间的能力边界。
> - 不设置独立 Risk Engine。
> - 普通执行错误直接由 Tool 抛出异常并反馈给 LLM。
> - 需求驱动抽象，不为“以后可能需要”提前增加架构层。

---

## 1. 项目定位

QMT Agent Trader 是一个面向量化交易场景的 Agent 系统。

目标不是开发通用 Agent Framework，而是构建一个：

- 可真实运行
- 可研究
- 可学习
- 可持续演进
- 人类开发者能够理解并亲自编写

的量化交易 Agent。

项目的重点是：

- Agent 行为
- Tool 设计
- 量化研究能力
- 交易业务逻辑
- QMT / XtQuant 集成

而不是：

- 自研 Agent Runtime
- 自研 LLM SDK
- 自研通用 Workflow Framework
- 为未来兼容性提前建立大量抽象层

---

## 2. 核心架构原则

### 2.1 Agent 是用户入口

用户只与 Agent 交互。

用户不直接操作：

- 行情接口
- QMT API
- 数据库
- 回测代码
- 量化分析模块

Agent 负责：

- 理解用户意图
- 选择合适的 Tool
- 组织分析过程
- 调用交易或研究能力
- 向用户解释结果

### 2.2 Tool 是能力边界

系统能力通过 Tool 暴露给 Agent。

例如：

- 获取行情
- 获取历史价格
- 查询持仓
- 查询订单
- 技术分析
- 基本面分析
- 回测
- 下单
- 撤单

Tool 表达的是：

> Agent 可以理解和调用的业务能力。

不是所有 Python 函数都应该成为 Tool。

推荐：

```text
get_market_snapshot()
get_price_history()
get_portfolio()
run_backtest()
place_order()
```

不推荐：

```text
xt_get_market_data_ex()
numpy_mean()
calculate_internal_helper()
```

Tool 内部可以自由调用普通 Python 函数、第三方库、QMT API 和数据存储。

### 2.3 通用 Agent Runtime 不自研

项目使用 OpenAI Agents SDK 作为 Agent Runtime。

因此不自行实现：

- Agent loop
- Runner
- Tool dispatch runtime
- function_call / function_call_output loop
- Session runtime
- Human-in-the-loop runtime
- Handoff runtime
- Agent-as-tool runtime
- Guardrails runtime
- Tracing runtime
- MCP runtime

这些属于通用 Agent 基础设施。

QMT Agent Trader 只在需要时使用这些能力，而不是重新实现一遍。

### 2.4 不重复包装第三方能力

如果第三方库已经提供合适的抽象，则直接使用。

例如：

```text
OpenAI Agents SDK
OpenAI Python SDK
DeepSeek Responses API
XtQuant
Polars / Pandas
NumPy / SciPy
DuckDB / Parquet
```

不增加以下无意义包装：

```text
QMTAgentRunner -> Agents SDK Runner
DeepSeekClient -> AsyncOpenAI
QMTTool -> Agents SDK FunctionTool
ModelProvider abstraction
Broker compatibility layer
```

判断标准：

> 如果一个抽象只是给第三方库换名字，则删除。

只有当一个抽象确实表达 QMT Agent Trader 的业务语义时，才保留。

### 2.5 依赖通过组合隔离，不通过包装隔离

第三方依赖由应用入口统一创建和组装。

例如：

```text
Application Entry
    |
    +-- AsyncOpenAI client
    +-- Agents SDK model/runtime config
    +-- Agent definitions
    +-- Tools
    +-- QMT services
    +-- Session
```

不同模块通过正常的 Python 依赖注入和对象组合保持边界。

不为“隔离”本身额外创建 Adapter / Provider / Wrapper 层。

---

## 3. 总体架构

```text
                         User
                           |
                           v
                    QMT Main Agent
                           |
                 OpenAI Agents SDK
                           |
            +--------------+--------------+
            |              |              |
         Session          HITL          Tracing
            |              |              |
            +--------------+--------------+
                           |
                         Tools
                           |
          +----------------+----------------+
          |                |                |
       Market           Research          Trading
          |                |                |
          v                v                v
      XtQuant /        Quant libs /        QMT
      Storage          Data / Storage
```

未来需要时，Agents SDK 还可以提供：

```text
Handoffs
Agent-as-tool
Guardrails
MCP
Multi-agent orchestration
```

这些能力按需求逐步启用。

---

## 4. 模型调用架构

模型调用链路：

```text
OpenAI Agents SDK
        |
        v
   AsyncOpenAI
        |
        | OpenAI-compatible
        | Responses API
        v
     DeepSeek
```

OpenAI Python SDK 负责：

- HTTP / API 通信
- API Key
- Base URL
- Responses API 请求

OpenAI Agents SDK 负责：

- Agent Runtime
- Tool 调度
- Agent Loop
- Session
- HITL
- Handoff
- Tracing
- 其他 Agent 通用能力

DeepSeek 负责：

- LLM 推理
- Responses API
- Function Calling

---

## 5. Agent 模块

Agent 模块不再实现 Runner。

其职责是：

> 定义 QMT Agent 是什么。

包括：

- Agent 名称
- Instructions / Prompt
- 可使用的 Tools
- 必要的 Agents SDK 配置
- 未来的 Handoff / Agent-as-tool 关系

建议初始结构：

```text
src/qmt_agent/

├── agents/
│   ├── main.py
│   └── prompts.py
```

### 5.1 `agents/main.py`

负责定义主 Agent。

概念上：

```python
Agent(
    name=...,
    instructions=...,
    tools=[...],
)
```

不负责：

- 自己执行 Agent loop
- 自己调用 `responses.create()`
- 自己处理 function_call
- 自己管理 Session
- 自己实现 HITL

这些由 Agents SDK Runtime 负责。

### 5.2 `agents/prompts.py`

负责保存 Agent Instructions / Prompt。

第一版保持简单。

例如：

```text
MAIN_AGENT_INSTRUCTIONS
```

不提前创建：

```text
PromptManager
PromptRegistry
PromptBuilder
```

除非后续真的出现需要动态组合的大量 Prompt。

---

## 6. Tool 模块

Tool 仍然是项目最重要的业务接口。

建议结构：

```text
tools/

├── market.py
├── portfolio.py
├── quant.py
├── research.py
└── trading.py
```

Tool 可以直接使用 Agents SDK 提供的 Function Tool 能力。

概念上：

```python
@function_tool
def get_market_snapshot(symbol: str):
    ...
```

而不是自己定义一套：

```text
QMTTool
ToolRegistry
ToolAdapter
ToolDispatcher
```

Tool 内部实现仍然可以拆分成普通 Python 函数和服务。

---

## 7. 交易授权

部分 Tool 需要用户确认。

例如：

无需确认：

```text
get_market_snapshot
get_price_history
get_portfolio
analyze_technicals
```

需要确认：

```text
run_backtest
place_order
cancel_order
```

`run_backtest` 会执行 Workspace 中的普通 RQAlpha Python 策略，因此审批是代码执行的授权边界，不是 Python sandbox。

交易授权使用 Agents SDK 的 Human-in-the-loop 能力。

执行流程：

```text
Agent
  |
  v
Tool Call
  |
  v
HITL Approval
  |
  +-- No  -> Cancel
  |
  +-- Yes
        |
        v
      Tool
        |
        v
       QMT
```

授权针对一次具体 Tool Call。

例如：

```text
买入 600519.SH 100 股
```

而不是：

```text
永久允许 place_order
```

当前回测链路保持直接组合：

```text
QMT Main Agent
      |
      v
run_backtest
      |
      v
qmt_agent.backtest.run_backtest
      |
      v
RQAlpha 6.3.0 -> QMTDataSource -> CNEquity 股票日线/复权因子
                                  + RQAlpha 原生市场语义
```

策略知识由现有 bootstrap / `--sync` 写入 `memory/rqalpha.md`。模型默认只接收
compact summary 和 Workspace 相对 artifact 路径，完整 analyser 表格保存在
`backtests/<run_id>/`。

---

## 8. 不设置独立 Risk Engine

第一版不设置：

```text
Risk Engine
Business Validation Layer
Execution Policy Layer
Safety Audit Layer
```

用户已经明确批准某次具体交易后，不再增加第二套隐藏的业务否决逻辑。

交易链路保持：

```text
Agent
  |
  v
Tool Call
  |
  v
HITL
  |
  v
Tool
  |
  v
QMT
```

---

## 9. Tool 错误处理

普通软件错误直接由 Tool 抛出异常。

例如：

```text
资金不足
证券代码错误
QMT 未连接
订单失败
非交易时间
接口超时
数据不可用
```

这些情况不需要单独设计：

```text
Risk Engine
Validation Service
Error Policy Layer
```

最简单的模型：

```text
Tool
  |
  +-- success -> result -> LLM
  |
  +-- exception -> error -> LLM
```

Agent / LLM 根据 Tool 执行结果决定如何向用户解释或继续处理。

原则：

> 普通软件错误用异常表达，不为错误处理创造新的业务架构层。

---

## 10. QMT / XtQuant 集成

QMT 模块只负责与 XtQuant / QMT 交互。

建议：

```text
qmt/

├── data.py
└── trader.py
```

例如：

```text
data.py
- 行情读取
- 历史数据读取

trader.py
- 持仓读取
- 订单读取
- 下单
- 撤单
```

Tool 可以调用这些模块。

QMT 模块不知道：

- Agent
- Prompt
- LLM
- Responses API
- Handoff
- HITL

依赖方向保持单向。

---

## 11. Storage

Storage 负责持久化项目实际需要的数据。

可能包括：

- 历史行情缓存
- 研究数据
- Agent Session
- 回测结果
- 用户配置
- 交易记录

存储技术优先使用成熟方案，例如：

```text
Parquet
DuckDB
SQLite
SQLAlchemy
```

不提前开发通用 Storage Framework。

---

## 12. Session

DeepSeek Responses API 不作为项目的长期对话状态存储。

会话状态由 Agents SDK Session 机制在客户端管理。

概念：

```text
User Input
    |
    v
Agents SDK Session
    |
    +-- previous history
    |
    v
Runner
    |
    v
DeepSeek Responses API
```

Session 后端按实际需求选择。

第一版可以从最简单方案开始。

---

## 13. Composition Root

应用入口负责组装所有组件。

建议：

```text
src/qmt_agent/main.py
```

职责：

```text
读取配置
    |
创建 AsyncOpenAI
    |
配置 DeepSeek model
    |
创建 QMT services
    |
加载 Tools
    |
创建 Agent
    |
创建 / 获取 Session
    |
调用 Agents SDK Runner
```

`main.py` 是项目的 Composition Root。

其他模块不需要知道整个应用是如何组装的。

---

## 14. 推荐目录结构

当前建议的最小目录：

```text
src/qmt_agent/

├── agents/
│   ├── main.py
│   └── prompts.py
│
├── tools/
│   ├── market.py
│   ├── portfolio.py
│   ├── quant.py
│   ├── research.py
│   └── trading.py
│
├── qmt/
│   ├── data.py
│   └── trader.py
│
├── storage/
│
├── config.py
│
└── main.py
```

这不是必须一次性全部创建的目录。

原则：

> 只有开始出现实际代码时才创建模块。

例如第一版只有：

```text
agents/
tools/
qmt/
main.py
config.py
```

也是完全合理的。

---

## 15. 当前明确不需要的模块

第一版不建立：

```text
agent/runner.py
agent/tools.py

runtime/
providers/
adapters/
interfaces/
registries/

risk/
risk_engine.py

validation/
business_validation.py

workflow/
memory_framework/
broker_abstraction/
model_provider/
```

如果未来真实需求证明其中某个抽象有独立职责，再增加。

---

## 16. 代码分类

整个项目可以简单分成三类代码。

### 16.1 第三方基础设施

直接复用：

```text
OpenAI Agents SDK
OpenAI Python SDK
DeepSeek Responses API
XtQuant
Polars / Pandas
NumPy / SciPy
DuckDB / Parquet
```

### 16.2 Agent Integration

项目自己写，但应保持薄：

```text
Agent definitions
Prompts
Tool definitions
SDK configuration
HITL configuration
Session configuration
```

### 16.3 QMT Trader Core

真正值得长期维护的业务代码：

```text
行情业务逻辑
量化研究逻辑
投资组合逻辑
回测逻辑
交易逻辑
QMT 集成
数据存储
```

原则：

> 我们研究 Agent，但不开发通用 Agent Framework。
> 我们真正开发的是 Quant Trading Agent。

---

## 17. Feature Roadmap

Roadmap 分为两条线。

### 17.1 业务能力

```text
1. Market Tools
2. Portfolio Tools
3. Research Tools
4. Backtest
5. Trading
6. Automated Trading
```

### 17.2 Agent Runtime 能力

```text
1. Single Agent
2. Session
3. Human-in-the-loop
4. Tracing
5. Guardrails
6. Agent-as-tool
7. Handoff
8. MCP
9. Multi-agent
```

Agent Runtime 能力优先复用 Agents SDK。

不将 roadmap 理解为：

> 将来我们要自己实现这些框架能力。

---

## 18. 软件工程原则

保持：

- 类型提示
- 清晰模块边界
- 单元测试
- 明确异常
- 配置管理
- 日志记录
- 简单依赖关系

避免：

- 为未来兼容而抽象
- 无意义 Wrapper
- Provider 层
- Broker 兼容层
- Interface 泛滥
- Registry 泛滥
- Factory 泛滥
- 自研 Agent Framework
- 自研 Tool Framework
- 自研 Validation Framework
- 自研 Risk Framework

核心判断标准：

> 能直接调用就直接调用。
> 能用普通 Python 函数解决就不要创建类。
> 能用异常表达就不要创建新的业务层。
> 需求驱动抽象，而不是为了未来提前设计。

---

## 19. 依赖方向

总体依赖方向：

```text
Application Entry
        |
        v
      Agents
        |
        v
       Tools
        |
        +----------+
        |          |
        v          v
       QMT       Storage
        |
        v
     XtQuant
```

允许：

```text
agents -> tools
tools -> qmt
tools -> storage
qmt -> XtQuant
```

避免：

```text
qmt -> tools
tools -> agents
storage -> agents
```

底层业务代码不依赖 Agent Runtime。

---

## 20. CNEquity 集成

CNEquity 作为 QMT Agent Trader 的运行时依赖安装，但其运维由用户自行管理。

人类通过以下透明 CLI passthrough 管理数据湖：

```text
qmt-agent data <CNEquity CLI arguments>
```

该命令从 QMT root 直接执行已安装的 CNEquity CLI。Main Agent 不拥有 CNEquity lifecycle tools；QMT 启动时只通过 `<QMT root>/configs/cnequity.toml` 附加一个只读 CNEquity stdio MCP server，Agent 仅通过该 MCP 查询 curated data。

QMT 不管理 CNEquity 配置、ingestion、retry、repair、status、locking 或 recovery。

---

## 21. 总结

QMT Agent Trader v0.2 的核心思想：

> Agent 是用户入口。
> Tool 是业务能力边界。
> OpenAI Agents SDK 负责通用 Agent Runtime。
> DeepSeek Responses API 负责模型推理。
> QMT / XtQuant 负责交易基础设施。
> 普通执行失败使用异常表达。
> 不增加不必要的 Risk / Validation / Adapter 层。
> 项目真正需要长期维护的是量化交易业务能力，而不是 Agent Framework。

最终追求：

- 简单
- 清晰
- 可学习
- 可测试
- 低耦合
- 高内聚
- 能持续演进
- 人类开发者能够理解并亲自编写
