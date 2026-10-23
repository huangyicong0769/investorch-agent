# InvestOrch Agent

[English](README.md)

InvestOrch Agent 是面向个人投资者的本地优先、human-in-the-loop 投资编排 Agent。长期目标是支持从研究、策略开发和回测，到组合决策、交易、持仓监控与复盘的完整流程。

**0.3.0** 增加 Session 协作、可复用模型连接与 ChatGPT 授权、按 Session 选模、持续上下文管理和更清晰的运行诊断。详见 [0.3.0 说明](docs/releases/0.3.0.zh-CN.md)、[模型配置](docs/Configuration.zh-CN.md)和[手动升级步骤](docs/Upgrade.zh-CN.md)。

## 0.3.0 包含

- Session 查找与历史读取、经审批的文字/图片消息，以及拥有独立审批的递归子 Session。
- API Key 或 ChatGPT 模型连接、独立 Agent 默认值，以及按 Session 选择模型与推理强度。
- 推理前与工具调用间的上下文压缩、实时 Main/辅助角色用量，以及持久化失败原因。
- 基于同一 Application 与 Runtime 的 Web 和 [Textual](https://github.com/Textualize/textual) TUI 两个一级界面。
- 持久化 Session，支持标题、归档与恢复、稳定头部 Fork、清除和上下文压缩。
- 不同 Session 的并发 Run，以及每个 Run 固定的 Steer 或 Queue 跟进模式。
- manual 或模型辅助 review 模式的人工审批。
- 追加式 JSONL 对话 Journal 与 SQLite 模型 continuation 状态。
- Workspace 范围内的探索、编辑、删除、计算、前台/后台命令、Todo、配置和 MCP registry Tool。
- 基于 [RQAlpha](https://github.com/ricequant/rqalpha) 的日频股票回测、可复现元数据和 Workspace 相对 artifact。
- 可选 [CNEquity](https://github.com/rootSunc/cnequity) extra，用于现有 RQAlpha 数据覆盖层和只读 MCP 集成。
- Portfolio 生命周期和持久追加式 Ledger，使用精确十进制数投影持仓与逻辑现金，通过经审批的 Agent 工作流维护，并提供只读 Web 视图。
- 六个受管 Workspace Skill、按需加载、独立安全审查与显式内置更新。
- Web 图片对话与 Workspace 读图。
- Web Setup、持久 Settings、MCP Integrations、System Status、离线诊断与备份创建/验证。

券商账户镜像、QMT Gateway、实盘交易、统一投资数据层和 Multi-Agent 编排属于后续工作。详见 [0.3.0 说明](docs/releases/0.3.0.zh-CN.md)和[产品路线图](docs/InvestOrch_Agent_Product_Roadmap.zh-CN.md)。

## 兼容性

CLI、配置、存储格式和 Tool 接口可能在后续 0.x 版本中调整。已有安装应按[升级说明](docs/Upgrade.zh-CN.md)操作，包括 Portfolio schema v6 的要求。

核心应用支持 macOS 和 Linux。Agent 的 `exec_command` 命令执行仅支持 macOS。未来通过运行在 Windows/QMT 环境中的 Gateway 连接 QMT。

## 环境要求

- Python 3.12–3.14（`>=3.12,<3.15`）
- [uv](https://docs.astral.sh/uv/)
- Responses 兼容的 API Key 连接，或具备资格并授权计划用量的 ChatGPT 账号

Runtime 使用 [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) 的 Responses 模型适配器。源码新安装不预选 Provider；Web Setup 接收初始模型连接和凭据，随后可在 Settings 为 Main、Title、Activity、Permission、Compact 各角色独立设置。

下方安装命令使用 Python 3.12。

## 安装

### PyPI

将 InvestOrch Agent 安装为 uv 管理的工具：

```bash
uv tool install --python 3.12 investorch==0.3.0
investorch web
```

安装后的命令是 `investorch`。替换已有安装前，先完成[升级说明](docs/Upgrade.zh-CN.md)中的备份和预检；0.2.0 模型 TOML 须手动转换。

### 源码安装

```bash
git clone --branch v0.3.0 https://github.com/huangyicong0769/investorch-agent.git
cd investorch-agent
uv sync --locked --python 3.12
```

下文使用 `uv run --locked investorch`。如果通过工具安装，请省略 `uv run --locked`，直接以 `investorch` 开始每条命令。

### 发行 wheel

从 [0.3.0 GitHub Release](https://github.com/huangyicong0769/investorch-agent/releases/tag/v0.3.0) 下载 `investorch-0.3.0-py3-none-any.whl`、`investorch-0.3.0.tar.gz` 和 `SHA256SUMS`。安装前校验 wheel：

```bash
# macOS
grep 'investorch-0.3.0-py3-none-any.whl$' SHA256SUMS | shasum -a 256 -c -
# Linux
grep 'investorch-0.3.0-py3-none-any.whl$' SHA256SUMS | sha256sum -c -

uv tool install --python 3.12 ./investorch-0.3.0-py3-none-any.whl
```

wheel 包含 Web 生产 bundle，运行时不需要 Node.js。源码构建 wheel 使用 `uv build --no-sources`；修改前端后，须先重建 Web bundle。

## 首次运行

Setup 分为 **Model Connections** 和 **Agent Defaults**：选择 API 连接或 **Continue with ChatGPT**，再为各角色指定模型、effort 和上下文窗口。纯 ChatGPT 配置无需 API Key。完整示例与登录方式见[配置指南](docs/Configuration.zh-CN.md)。

### 1. 打开 Web Setup

```bash
uv run --locked investorch web
```

打开 <http://127.0.0.1:1334>。新实例会持续提供 Setup 页面，此时尚未启动 Agent。添加 API Key 连接或授权 ChatGPT，选择初始 Agent 默认值并保存。凭据保存在 `~/.investorch/investorch.toml`，后续读取不会向浏览器回显明文。

### 2. 测试并启动

可选的连接测试会发送一次小型真实模型请求并报告结果；保存配置本身不代表连接成功。完成 Setup 后启动 ApplicationHost 并进入 Chats。配置错误或旧数据不兼容时，页面进入恢复状态并提供修复指引，不会覆盖原实例。

模型 Endpoint、名称与 Secret 引用须显式配置。缺少模型设置时进入 Setup；未知字段或无效值无法通过当前 Schema 校验。详见[升级说明](docs/Upgrade.zh-CN.md)。

### 3. 启动界面

后续 Web 启动使用已保存设置。Server 只监听 loopback；Python Schema 的默认地址为 <http://127.0.0.1:1334>：

```bash
uv run --locked investorch web
uv run --locked investorch web --port 8000
```

模型设置完成后，TUI 是同等支持的一级界面：

```bash
uv run --locked investorch
```

plain console 用于诊断：

```bash
uv run --locked investorch --plain
```

## Portfolio

Portfolio 将实际且已确认的投资事实保存在 `state/portfolio.db`，也可使用配置的 state 目录。Agent Tool 创建和初始化 Portfolio、记录成交/资金流/收入、追加纠错，以及在 Portfolio 之间原子转移持仓或现金。持仓与逻辑现金由追加式 Ledger 重放得到；历史行不修改或删除。每个 Portfolio 使用单一记账币种、精确十进制数，并明确表示未知成本。

Web Portfolios 页面展示元数据、持仓、现金和最近 Ledger。**New Portfolio** 与 **Ask Agent** 进入 Agent 工作流，经济事实变更沿用正常审批路径，关联 Portfolio 身份随 Session 持久化。这些视图不计算市值、NAV、业绩或券商购买力，记录成交也不意味着下单。

详见 [Portfolio 领域模型](docs/Portfolio_Domain_Model.md)、[UI 与 Session 上下文](docs/Portfolio_UI_and_Session_Context.md)及 [schema v6 升级](docs/Upgrade.zh-CN.md#portfolio-v1v6)。

## Skills

首次初始化创建 `MEMORY.md`，并在 `workspace/skills/` 安装六个内置 Skill：`skill-creator`、`skill-installer`、`investorch-configuration`、`investorch-portfolio`、`rqalpha-strategy`、`qmt-strategy`。Memory 保存用户/项目上下文，Skill 保存可复用工作流。

```bash
uv run --locked investorch --update
```

在 System Status 或 `investorch upgrade check` 查看受管 Skill 差异后，显式运行此命令。它以随包内容确定性替换内置 Skill，保留启停状态，不调用模型或初始化 Portfolio，完成后退出。随包内容优先于本地修改和版本；定制内置 Skill 应 fork 为新名称。外部/自建 Skill 和用户 Memory 不受影响。

Main Agent 在应用启动时获得 metadata，并按需加载正文。安装、替换、启停、删除和更新后，须重启应用才能刷新 catalog。外部/自建候选需要独立安全审查和正常审批；WARN 必须人工审批，BLOCK 禁止安装。安装不授予脚本执行权限。

QMT Skill 生成供用户审查、人工部署到大 QMT 的策略产物；InvestOrch 不执行 QMT 下单。

## 图片对话

Web 对话支持选择、粘贴或拖放 JPEG、PNG、GIF、WebP，也支持纯图片消息。图片随 Session 历史保存，Agent 可通过 `explore` 读取 Workspace 图片；外部图片经点击才加载。图片限制在 `[images]` 中配置，重启生效，详见[图片架构说明](docs/InvestOrch_Agent_Architecture.zh-CN.md#对话图片内容)。

## Session Tool

Agent 可用 `list_sessions` 发现精确 Session ID，用 `read_session` 读取历史，用经审批的 `send_message` 向当前 Root 内另一未归档 Session 提交文本/图片。例如 `send_message(target_session_id="<exact-id>", text="检查这张图", images=[{"source":"workspace","path":"charts/result.png"}])`。已有上传图片可通过发送方 Journal 的 `seq` 与 `image_index` 转发，也支持纯图。接收方看到可信发送身份，Web/TUI 在原消息视图显示来源。需要结果时读取后续历史或显式回复；回执只确认提交，Queue 不跨重启保留。契约与限制见[Session 消息](docs/InvestOrch_Agent_Architecture.zh-CN.md#session-消息)。

## Settings 与 System Status

- **Settings** 使用 Python Schema 提供的类型、默认值、约束和生命周期编辑持久配置。Reset 删除本地覆盖；热更新影响后续适用操作，需要重启的修改保存为下次启动值。临时 Permission/follow-up 默认值仅属于当前进程。0.3.0 中，输入框右下的模型/推理强度入口及 `/model`、`/effort` 修改选中 Session 的模型 Draft；下一次 Run 才持久化，活动 Run 保持冻结模型。
- **Integrations** 管理独立的 `mcp.toml`，仅支持 `streamable_http`。未修改的 URL/Header 隐藏凭据会保留，修改需要重启。未实际探测的连接显示为 Unknown。
- **Skills** 展示与检查已安装内容。Create/Install 及具体 Skill 的 Edit/Fork 打开正常 Agent Session，沿用现有审查与审批。用户可直接启停，或确认删除自定义 Skill。
- **System Status** 展示与离线 CLI 相同的本地检查：

```bash
uv run --locked investorch doctor
uv run --locked investorch doctor --json
uv run --locked investorch upgrade check
```

Doctor 只读，不调用模型、连接 MCP、更新 Skill 或迁移数据库。退出码 `0` 表示 healthy/disabled，`1` 表示 warning/unknown，`2` 表示 error。离线备份、验证与向独立 Root 恢复参见[升级说明](docs/Upgrade.zh-CN.md)和[备份与恢复](docs/Backup_and_Recovery.zh-CN.md)。默认备份排除包含原始凭据的配置。

## 可选 CNEquity 集成

仅在使用当前 CNEquity CLI passthrough、只读 MCP 集成或 RQAlpha 覆盖层时安装可选 extra：

```bash
uv sync --locked --python 3.12 --extra cnequity
```

使用 wheel 安装时启用 extra：

```bash
uv tool install --python 3.12 './investorch-0.3.0-py3-none-any.whl[cnequity]'
```

CNEquity 是可选数据后端。更广泛的集成要等待上游行为足够稳定后再评估。

## 数据维护

### RQAlpha bundle

原生 RQAlpha 回测读取 `backtest.rqalpha_bundle_dir` 指定的 bundle。Python Schema 默认将其解析为 `~/.investorch/.rqalpha/bundle`。RQAlpha 的 `-d` 参数接收父目录，并在其下自动追加 `bundle`，因此对应的命令行路径是 `~/.investorch/.rqalpha`。

下载 RQAlpha 每月更新的 bundle，并在回测前检查数据：

```bash
uv run --locked rqalpha download-bundle -d ~/.investorch/.rqalpha
uv run --locked rqalpha check-bundle -d ~/.investorch/.rqalpha
```

RQDatac 用户可以使用 `rqalpha update-bundle` 更新同一目录；连接与并发参数见 `uv run --locked rqalpha update-bundle --help`。

### CNEquity 数据湖

`investorch data` 会把后续参数转发给已安装的 [CNEquity CLI](https://rootsunc.github.io/cnequity/)，并从 InvestOrch root 运行。因此默认配置路径会解析为 `~/.investorch/configs/cnequity.toml`。

初始化并验证数据配置：

```bash
uv run --locked investorch data config init
uv run --locked investorch data config validate
uv run --locked investorch data doctor
```

首次建湖。`quick` profile 会获取所有股票最近三年的数据；需要完整历史时使用 `--profile full`：

```bash
uv run --locked investorch data init --profile quick
```

执行日常维护并检查数据健康状态：

```bash
uv run --locked investorch data run daily
uv run --locked investorch data status --datasets
uv run --locked investorch data verify
uv run --locked investorch data audit --full
```

清理前先预览可删除的 staging 与 snapshot：

```bash
uv run --locked investorch data clean --dry-run
```

使用 `uv run --locked investorch data --help` 和 `<command> --help` 查看 backfill、retry、compact、source probe、catalog、stats、query 等上游操作。

## 本地优先边界

配置、Workspace 文件、Session 元数据、Journal、日志和生成的 artifact 默认保存在本地。已配置的模型 endpoint 与 MCP server 会接收其调用所需内容。Web server 只监听 loopback；HTTP 写请求要求本地 Host、浏览器同源与 JSON 内容，这不构成经过认证的 LAN 服务。

## 前端开发

前端开发使用 `frontend/.node-version` 固定的 Node.js 版本。启动 Python Web server 后，在另一个终端启动 Vite：

```bash
cd frontend
npm ci
npm run dev
```

打包前端改动前执行：

```bash
npm run typecheck
npm run lint
npm run build
```

`npm run build` 会替换 `src/investorch/web/static` 中已跟踪的生产 bundle。运行已构建 wheel 不需要 Node.js。

## 验证与打包

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pytest
uv build --no-sources
```

## 第三方基础

InvestOrch Agent 构建于以下项目之上：

- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)：Agent 执行、Tool、Session、HITL 与 MCP 集成。
- [RQAlpha](https://github.com/ricequant/rqalpha)：回测引擎。
- [CNEquity](https://github.com/rootSunc/cnequity)：可选 A 股数据后端。
- [FastAPI](https://github.com/fastapi/fastapi)、[React](https://github.com/facebook/react) 与 [Vite](https://github.com/vitejs/vite)：Web 客户端。
- [Textual](https://github.com/Textualize/textual)：TUI。
- [uv](https://github.com/astral-sh/uv)：Python 环境、lockfile 与打包工作流。

InvestOrch Agent 自有代码和文档采用 [Apache-2.0](LICENSE) 许可证，第三方组件仍适用各自的许可证。RQAlpha 及其 RQRisk 依赖对各自定义的非商业与商业用途采用项目自己的许可条款，InvestOrch Agent 的许可证不会覆盖这些条款。详情及随包 Web 客户端的许可清单见[第三方声明](THIRD_PARTY_NOTICES.md)。

## 人与 AI 的开发协作

InvestOrch Agent 是由人主导、AI 辅助开发的个人研究项目。个人开发者的时间、知识和工程能力有限，因此借助 AI 扩展调研、设计、实现和验证能力；项目所有者仍深度参与产品设计、投资领域建模、架构、安全边界和关键代码编写。

- 人负责产品方向、投资领域判断、架构取舍、关键代码编写，以及最终 review 与验收。
- AI 参与代码调查、方案讨论、辅助实现、测试和文档编写。
- 需求不清时先讨论再实现，最终改动由人审查决定。

## 文档

- [0.3.0 发行说明](docs/releases/0.3.0.zh-CN.md)与[变更记录](CHANGELOG.zh-CN.md)。
- [维护者发布指引](docs/Publishing.md)：review、GitHub Release 与 PyPI 发布。
- [升级说明](docs/Upgrade.zh-CN.md)与[备份和恢复](docs/Backup_and_Recovery.zh-CN.md)。
- [架构](docs/InvestOrch_Agent_Architecture.zh-CN.md)：已实现系统与当前边界。
- [Runtime / Session 执行模型](docs/Runtime_Session_Execution_Model.zh-CN.md)：生命周期与并发规则。
- [产品路线图](docs/InvestOrch_Agent_Product_Roadmap.zh-CN.md)：已确认方向、待决策事项和候选主题。

## 投资免责声明

InvestOrch Agent 是个人研究与软件项目，不提供投资、法律、税务或会计建议，也不推荐或招揽任何证券或交易。

市场数据、第三方数据、模型输出、计算和回测可能不准确、不完整、存在延迟，或受到假设与事后偏差影响。历史表现和回测结果不代表未来结果。

采取行动前请独立核验数据与输出。投资决策、账户凭证、系统配置、监管合规及由此产生的收益或损失均由使用者负责。连接真实资金前，请先在非生产账户和环境中测试。
