# InvestOrch Agent

[English](README.md)

InvestOrch Agent 是面向个人投资者的本地优先、human-in-the-loop 投资编排 Agent。长期目标是支持从研究、策略开发和回测，到组合决策、交易、持仓监控与复盘的完整流程。

0.1.0 是这一产品方向的早期预览，目前还不是端到端交易系统。

## 0.1.0 已包含

- 基于同一 Application 与 Runtime 的 Web 和 [Textual](https://github.com/Textualize/textual) TUI 两个一级界面。
- 持久化 Session，支持标题、归档与恢复、稳定头部 Fork、清除和上下文压缩。
- 不同 Session 的并发 Run，以及每个 Run 固定的 Steer 或 Queue 跟进模式。
- manual 或模型辅助 review 模式的人工审批。
- 追加式 JSONL 对话 Journal 与 SQLite 模型 continuation 状态。
- Workspace 范围内的探索、编辑、删除、计算、前台/后台命令、Todo、配置和 MCP registry Tool。
- 基于 [RQAlpha 6.3.0](https://github.com/ricequant/rqalpha) 的日频股票回测、可复现元数据和 Workspace 相对 artifact。
- 可选 [CNEquity](https://github.com/rootSunc/cnequity) extra，用于现有 RQAlpha 数据覆盖层和只读 MCP 集成。

组合/账户访问、QMT Gateway、实盘交易、统一投资数据层和 Multi-Agent 编排属于后续工作。具体方向见[产品路线图](docs/InvestOrch_Agent_Product_Roadmap.zh-CN.md)。

## 状态与兼容性

0.1.0 是早期预览。CLI、配置、存储格式和 Tool 接口可能在后续 0.x 版本中调整。

核心应用目前在 macOS 本机和 Ubuntu CI 中得到验证，运行目标为 macOS 和 Linux 环境。未来通过运行在 Windows/QMT 环境中的 Gateway 连接 QMT。

## 环境要求

- Python 3.12 或更高版本
- [uv](https://docs.astral.sh/uv/)
- 用于随包模型配置的 [DeepSeek API Key](https://platform.deepseek.com/api_keys)

Runtime 使用 [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) 的 Responses 模型适配器。随包配置默认使用 [DeepSeek](https://api-docs.deepseek.com/)，所有随包 Agent 角色都从名为 `DEEPSEEK_API_KEY` 的本地 secret 读取 API Key。

## 安装

### GitHub Release

从 [`v0.1.0` GitHub Release](https://github.com/huangyicong0769/investorch-agent/releases/tag/v0.1.0) 下载 `investorch-0.1.0-py3-none-any.whl` 和 `SHA256SUMS`。在两个文件所在的目录中校验 wheel：

```bash
# macOS
grep 'investorch-0.1.0-py3-none-any.whl$' SHA256SUMS | shasum -a 256 -c -

# Linux
grep 'investorch-0.1.0-py3-none-any.whl$' SHA256SUMS | sha256sum -c -
```

校验通过后，将 wheel 安装为由 uv 管理的工具：

```bash
uv tool install ./investorch-0.1.0-py3-none-any.whl
```

安装后的命令是 `investorch`。

### 从源码检出

```bash
git clone https://github.com/huangyicong0769/investorch-agent.git
cd investorch-agent
uv sync --locked
```

下文使用源码检出形式 `uv run investorch`。如果通过 Release wheel 安装，请省略 `uv run`，直接以 `investorch` 开始每条命令。

## 首次运行

### 1. 初始化本地状态

```bash
uv run investorch web
```

首次运行会在 `~/.investorch` 下创建本地配置、Workspace 和状态目录，然后退出。这是正常的初始化流程。

### 2. 配置必需的 API Key

先在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 创建 API Key。然后用文本编辑器打开 `~/.investorch/investorch.toml`，在已有的 `[secrets]` 标题下添加：

```toml
[secrets]
DEEPSEEK_API_KEY = "替换为你的API-Key"
```

将示例值替换为真实 API Key，保留两侧引号，不要分享或提交此文件。随包配置中的 Main、Title、Activity、Bootstrap、Permission 和 Compact Agent 都使用这个 secret。

### 3. 启动界面

保存配置后，再次启动 Web 界面：

```bash
uv run investorch web
```

Web 客户端只监听 loopback；随包默认地址为 <http://127.0.0.1:1334>：

```bash
uv run investorch web
uv run investorch web --port 8000
```

TUI 是同等支持的一级界面：

```bash
uv run investorch
```

plain console 用于诊断：

```bash
uv run investorch --plain
```

## 同步 Workspace 引导文件

项目升级时，随包提供的 `MEMORY.md`、`memory/configuration.md` 和 `memory/rqalpha.md` 模板可能发生变化。使用以下命令将更新合并到 `~/.investorch/workspace` 中的对应文件：

```bash
uv run investorch --sync
```

`--sync` 使用 `[models.bootstrap]` 配置的模型应用当前项目规则，同时保留用户长期维护的内容。每个结果通过验证后，命令即退出。如果已有文件发生变化，原文件会保存在 `~/.investorch/state/bootstrap-backups/<timestamp>/` 下，命令也会显示具体备份路径。

如果需要跳过模型，直接用随包模板替换这些 Workspace 文件：

```bash
uv run investorch --sync-force
```

`--sync-force` 会丢弃这些目标文件的当前内容，并在替换前将原文件备份到同一备份目录。

## 可选 CNEquity 集成

仅在使用当前 CNEquity CLI passthrough、只读 MCP 集成或 RQAlpha 覆盖层时安装可选 extra：

```bash
uv sync --locked --extra cnequity
```

如果使用 GitHub Release wheel，请在安装工具时启用 extra：

```bash
uv tool install './investorch-0.1.0-py3-none-any.whl[cnequity]'
```

CNEquity 是可选数据后端。更广泛的集成要等待上游行为足够稳定后再评估。

## 数据维护

### RQAlpha bundle

原生 RQAlpha 回测读取 `backtest.rqalpha_bundle_dir` 指定的 bundle。随包配置会将其解析为 `~/.investorch/.rqalpha/bundle`。RQAlpha 的 `-d` 参数接收父目录，并在其下自动追加 `bundle`，因此对应的命令行路径是 `~/.investorch/.rqalpha`。

下载 RQAlpha 每月更新的 bundle，并在回测前检查数据：

```bash
uv run rqalpha download-bundle -d ~/.investorch/.rqalpha
uv run rqalpha check-bundle -d ~/.investorch/.rqalpha
```

RQDatac 用户可以使用 `rqalpha update-bundle` 更新同一目录；连接与并发参数见 `uv run rqalpha update-bundle --help`。

### CNEquity 数据湖

`investorch data` 会把后续参数转发给已安装的 [CNEquity CLI](https://rootsunc.github.io/cnequity/)，并从 InvestOrch root 运行。因此默认配置路径会解析为 `~/.investorch/configs/cnequity.toml`。

初始化并验证数据配置：

```bash
uv run investorch data config init
uv run investorch data config validate
uv run investorch data doctor
```

首次建湖。`quick` profile 会获取所有股票最近三年的数据；需要完整历史时使用 `--profile full`：

```bash
uv run investorch data init --profile quick
```

执行日常维护并检查数据健康状态：

```bash
uv run investorch data run daily
uv run investorch data status --datasets
uv run investorch data verify
uv run investorch data audit --full
```

清理前先预览可删除的 staging 与 snapshot：

```bash
uv run investorch data clean --dry-run
```

使用 `uv run investorch data --help` 和 `<command> --help` 查看 backfill、retry、compact、source probe、catalog、stats、query 等上游操作。

## 本地优先边界

配置、Workspace 文件、Session 元数据、Journal、日志和生成的 artifact 默认保存在本地。已配置的模型 endpoint 与 MCP server 会接收其调用所需内容。0.1.0 的 Web server 只监听 loopback。

## 前端开发

启动 Python Web server 后，在另一个终端启动 Vite：

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

- [架构](docs/InvestOrch_Agent_Architecture.zh-CN.md)：已实现系统与当前边界。
- [Runtime / Session 执行模型](docs/Runtime_Session_Execution_Model.zh-CN.md)：生命周期与并发规则。
- [产品路线图](docs/InvestOrch_Agent_Product_Roadmap.zh-CN.md)：已确认方向、待决策事项和候选主题。

## 投资免责声明

InvestOrch Agent 是个人研究与软件项目，不提供投资、法律、税务或会计建议，也不推荐或招揽任何证券或交易。

市场数据、第三方数据、模型输出、计算和回测可能不准确、不完整、存在延迟，或受到假设与事后偏差影响。历史表现和回测结果不代表未来结果。

采取行动前请独立核验数据与输出。投资决策、账户凭证、系统配置、监管合规及由此产生的收益或损失均由使用者负责。连接真实资金前，请先在非生产账户和环境中测试。
