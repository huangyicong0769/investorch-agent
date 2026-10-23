# InvestOrch Agent

[简体中文](README.zh-CN.md)

InvestOrch Agent is a local-first, human-in-the-loop investment orchestration agent for an individual investor. Its long-term goal is to support the full path from research, strategy development, and backtesting to portfolio decisions, trading, position monitoring, and review.

**0.3.0** adds multi-agent orchestration, reusable model connections and ChatGPT authorization, per-Session model selection, continuous context management, and clearer runtime diagnostics. See [0.3.0 notes](docs/releases/0.3.0.md), [model configuration](docs/Configuration.md) and the [manual upgrade procedure](docs/Upgrade.md).

## What 0.3.0 includes

- Web and [Textual](https://github.com/Textualize/textual) TUI clients, plus a diagnostic plain console.
- Multi-agent task delegation and parallel research, with subagent progress, conversation and results accessible from Web.
- API-key or ChatGPT model connections, with model and reasoning-effort choices for each conversation.
- Persistent conversations, archive/restore and history forks, with context compaction for long tasks.
- Workspace file editing, image conversations, command execution and task planning.
- [RQAlpha](https://github.com/ricequant/rqalpha) daily stock backtesting and optional [CNEquity](https://github.com/rootSunc/cnequity) market-data integration.
- Portfolio records for confirmed trades, cash flows, holdings and cash, with an auditable Ledger.
- Reusable Skills and external MCP integrations.
- Model usage, failure diagnostics, local backup and recovery.

Broker/account mirroring, a QMT gateway, live trading and a unified investment data layer remain future work. See the [0.3.0 notes](docs/releases/0.3.0.md) and [Product Roadmap](docs/InvestOrch_Agent_Product_Roadmap.md).

## Compatibility

The core supports macOS and Linux. Agent shell-command execution supports macOS only. Existing installations should follow [Upgrade](docs/Upgrade.md) before changing versions.

Conversations, configuration and generated files are stored locally. Configured model providers and MCP services receive the content needed for their calls. Web listens on loopback only.

## Requirements

- Python 3.12–3.14 (`>=3.12,<3.15`)
- [uv](https://docs.astral.sh/uv/)
- A Responses-compatible API-key connection, or an eligible ChatGPT account authorizing plan usage

The installation commands below select Python 3.12.

## Installation

### PyPI

Install InvestOrch Agent as a uv-managed tool:

```bash
uv tool install --python 3.12 investorch==0.3.0
investorch web
```

The installed command is `investorch`. For an existing installation, complete the backup and preflight steps in [Upgrade](docs/Upgrade.md) before replacing it; 0.2.0 model TOML requires manual conversion.

### Source checkout

```bash
git clone --branch v0.3.0 https://github.com/huangyicong0769/investorch-agent.git
cd investorch-agent
uv sync --locked --python 3.12
```

The examples below use `uv run --locked investorch`. For a tool installation, omit `uv run --locked` and start each command with `investorch`.

### Release wheel

Download `investorch-0.3.0-py3-none-any.whl`, `investorch-0.3.0.tar.gz` and `SHA256SUMS` from the [0.3.0 GitHub Release](https://github.com/huangyicong0769/investorch-agent/releases/tag/v0.3.0). Verify the wheel before installing it:

```bash
# macOS
grep 'investorch-0.3.0-py3-none-any.whl$' SHA256SUMS | shasum -a 256 -c -
# Linux
grep 'investorch-0.3.0-py3-none-any.whl$' SHA256SUMS | sha256sum -c -

uv tool install --python 3.12 ./investorch-0.3.0-py3-none-any.whl
```

A wheel includes the production Web bundle and needs no Node.js runtime. To build a wheel from source, use `uv build --no-sources`; rebuild the Web bundle first when changing frontend code.

## First run

Start Web:

```bash
uv run --locked investorch web
```

Open <http://127.0.0.1:1334> and complete Setup. Add an API-key connection or choose **Continue with ChatGPT**, then select the initial Agent models. ChatGPT-only setups need no API key. You can test the connection before opening Chats; later changes are available in Settings.

After setup, start the TUI or diagnostic plain console:

```bash
uv run --locked investorch
uv run --locked investorch --plain
```

## Optional CNEquity integration

Install the optional extra to use CNEquity market data:

```bash
uv sync --locked --python 3.12 --extra cnequity
```

For a wheel installation, include the extra when installing the tool:

```bash
uv tool install --python 3.12 './investorch-0.3.0-py3-none-any.whl[cnequity]'
```

## Frontend development

Use the Node.js version pinned in `frontend/.node-version` for frontend development. Run the Python Web server, then start Vite in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Before packaging a frontend change:

```bash
npm run typecheck
npm run lint
npm run build
```

`npm run build` replaces the committed production bundle in `src/investorch/web/static`. Node.js is not required to run an already built wheel.

## Verification and packaging

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pytest
uv build --no-sources
```

## Third-party foundations

InvestOrch Agent is built on these projects:

- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) for Agent execution, Tools, sessions, HITL, and MCP integration.
- [RQAlpha](https://github.com/ricequant/rqalpha) for backtesting.
- [CNEquity](https://github.com/rootSunc/cnequity) as the optional A-share data backend.
- [FastAPI](https://github.com/fastapi/fastapi), [React](https://github.com/facebook/react), and [Vite](https://github.com/vitejs/vite) for the Web client.
- [Textual](https://github.com/Textualize/textual) for the TUI.
- [uv](https://github.com/astral-sh/uv) for Python environment, lockfile, and package workflows.

InvestOrch Agent's own code and documentation are licensed under [Apache-2.0](LICENSE). Third-party components retain their own licenses. In particular, RQAlpha and its RQRisk dependency use project-specific terms for their defined non-commercial and commercial uses; InvestOrch Agent's license does not override those terms. See [Third-party notices](THIRD_PARTY_NOTICES.md) for details and the bundled Web client license inventory.

## Human-AI development collaboration

InvestOrch Agent is a human-led personal research project developed with AI assistance. A solo developer has limited time, knowledge, and engineering capacity, so AI helps extend the work that can be done in research, design, implementation, and validation. The owner remains deeply involved in product design, investment-domain modeling, architecture, safety boundaries, and key code.

- The owner defines the product direction, makes investment-domain and architecture decisions, writes key code, and performs final review and acceptance.
- AI assists with codebase investigation, design discussion, implementation, testing, and documentation.
- Unclear requirements are discussed before implementation, and accepted changes remain subject to human review.

## Documentation

- [0.3.0 release notes](docs/releases/0.3.0.md) and [Changelog](CHANGELOG.md).
- [Maintainer release procedure](docs/Publishing.md): review, GitHub releases and PyPI publishing.
- [Configuration](docs/Configuration.md), [Upgrade](docs/Upgrade.md) and [Backup and Recovery](docs/Backup_and_Recovery.md).
- [Architecture](docs/InvestOrch_Agent_Architecture.md): the implemented system and current boundaries.
- [Runtime / Session Execution Model](docs/Runtime_Session_Execution_Model.md): lifecycle and concurrency rules.
- [Product Roadmap](docs/InvestOrch_Agent_Product_Roadmap.md): confirmed directions, open decisions, and candidate themes.

## Investment disclaimer

InvestOrch Agent is a personal research and software project. It does not provide investment, legal, tax, or accounting advice, and it does not recommend or solicit any security or transaction.

Market data, third-party data, model output, calculations, and backtests may be inaccurate, incomplete, delayed, or affected by assumptions and hindsight. Historical and backtested performance does not indicate future results.

Independently verify data and outputs before acting. You are responsible for investment decisions, credentials, configuration, regulatory compliance, and any resulting gains or losses. Test with non-production accounts and environments before connecting real capital.
