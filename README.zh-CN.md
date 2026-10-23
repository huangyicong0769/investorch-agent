# InvestOrch Agent

[English](README.md)

InvestOrch Agent 是面向个人投资者的本地优先、human-in-the-loop 投资编排 Agent。长期目标是支持从研究、策略开发和回测，到组合决策、交易、持仓监控与复盘的完整流程。

**0.3.0** 增加多代理编排、可复用模型连接与 ChatGPT 授权、按 Session 选模、持续上下文管理和更清晰的运行诊断。详见 [0.3.0 说明](docs/releases/0.3.0.zh-CN.md)、[模型配置](docs/Configuration.zh-CN.md)和[手动升级步骤](docs/Upgrade.zh-CN.md)。

## 0.3.0 包含

- Web 和 [Textual](https://github.com/Textualize/textual) TUI，以及诊断用 plain console。
- 多代理任务委派与并行研究，可在 Web 查看子任务进度、对话和结果。
- API Key 或 ChatGPT 模型连接，每个对话可选择模型和推理强度。
- 持久化对话、归档恢复与历史分支，通过上下文压缩支持长任务。
- Workspace 文件编辑、图片对话、命令执行与任务计划。
- [RQAlpha](https://github.com/ricequant/rqalpha) 日频股票回测；保留 [CNEquity](https://github.com/rootSunc/cnequity) 集成，发行包不安装 CNEquity 依赖。
- 记录已确认成交、资金流、持仓与现金的 Portfolio，以及可审计的 Ledger。
- 可复用 Skills 和外部 MCP 集成。
- 模型用量、失败诊断、本地备份与恢复。

券商账户镜像、QMT Gateway、实盘交易和统一投资数据层属于后续工作。详见 [0.3.0 说明](docs/releases/0.3.0.zh-CN.md)和[产品路线图](docs/InvestOrch_Agent_Product_Roadmap.zh-CN.md)。

## 兼容性

核心应用支持 macOS 和 Linux，Agent 命令执行仅支持 macOS。已有安装应先按[升级说明](docs/Upgrade.zh-CN.md)操作，再更换版本。

对话、配置与生成的文件保存在本地。已配置的模型 Provider 和 MCP 服务会接收调用所需的内容。Web 只监听 loopback。

## 环境要求

- Python 3.12–3.14（`>=3.12,<3.15`）
- [uv](https://docs.astral.sh/uv/)
- Responses 兼容的 API Key 连接，或具备资格并授权计划用量的 ChatGPT 账号

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

启动 Web：

```bash
uv run --locked investorch web
```

打开 <http://127.0.0.1:1334> 完成 Setup。添加 API Key 连接或选择 **Continue with ChatGPT**，再选择初始 Agent 模型。纯 ChatGPT 配置无需 API Key。进入 Chats 前可测试连接，后续设置在 Settings 中修改。

完成设置后，也可启动 TUI 或诊断用 plain console：

```bash
uv run --locked investorch
uv run --locked investorch --plain
```

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
- [配置指南](docs/Configuration.zh-CN.md)、[升级说明](docs/Upgrade.zh-CN.md)与[备份和恢复](docs/Backup_and_Recovery.zh-CN.md)。
- [架构](docs/InvestOrch_Agent_Architecture.zh-CN.md)：已实现系统与当前边界。
- [Runtime / Session 执行模型](docs/Runtime_Session_Execution_Model.zh-CN.md)：生命周期与并发规则。
- [产品路线图](docs/InvestOrch_Agent_Product_Roadmap.zh-CN.md)：已确认方向、待决策事项和候选主题。

## 投资免责声明

InvestOrch Agent 是个人研究与软件项目，不提供投资、法律、税务或会计建议，也不推荐或招揽任何证券或交易。

市场数据、第三方数据、模型输出、计算和回测可能不准确、不完整、存在延迟，或受到假设与事后偏差影响。历史表现和回测结果不代表未来结果。

采取行动前请独立核验数据与输出。投资决策、账户凭证、系统配置、监管合规及由此产生的收益或损失均由使用者负责。连接真实资金前，请先在非生产账户和环境中测试。
