# InvestOrch Agent

[简体中文](README.zh-CN.md)

InvestOrch Agent is a local-first, human-in-the-loop investment orchestration agent for an individual investor. Its long-term goal is to support the full path from research, strategy development, and backtesting to portfolio decisions, trading, position monitoring, and review.

InvestOrch Agent **0.2.0** provides Web, TUI and plain-console clients, Portfolio accounting, managed Skills, image conversations, and configuration/recovery tools.

## What 0.2.0 includes

- First-class Web and [Textual](https://github.com/Textualize/textual) TUI clients backed by the same application and runtime.
- Persistent sessions with titles, archive and restore, stable-head fork, clear, and context compaction.
- Concurrent runs across sessions, with per-run Steer or Queue follow-up behavior.
- Human approval in manual or model-assisted review mode.
- Append-only JSONL conversation journals and SQLite model-continuation state.
- Workspace-scoped exploration, editing, deletion, calculation, foreground/background command execution, Todo state, configuration, and MCP registry tools.
- Daily stock backtesting with [RQAlpha](https://github.com/ricequant/rqalpha), reproducibility metadata, and workspace-relative artifacts.
- An optional [CNEquity](https://github.com/rootSunc/cnequity) extra for the existing RQAlpha data overlay and read-only MCP integration.
- Portfolio lifecycle and a durable append-only Ledger with exact-decimal holdings and logical cash, approved Agent workflows, and read-only Web views.
- Six managed Workspace Skills, on-demand loading, independent safety review, and explicit built-in updates.
- Web image conversations and Workspace image reading.
- Web Setup, persistent Settings, MCP Integrations, System Status, offline diagnostics, and backup create/verify.

Broker/account mirroring, a QMT gateway, live trading, a unified investment data layer, and Multi-Agent orchestration remain future work. See the [0.2.0 release notes](docs/releases/0.2.0.md) and [Product Roadmap](docs/InvestOrch_Agent_Product_Roadmap.md).

## Compatibility

The CLI, configuration, storage formats, and Tool interfaces may change in later 0.x releases. Existing installations should follow [Upgrade](docs/Upgrade.md), including the Portfolio schema v6 requirements.

The core supports macOS and Linux. Agent shell-command execution through `exec_command` supports macOS only. Future QMT connectivity will use a gateway running with QMT on Windows.

## Requirements

- Python 3.12–3.14 (`>=3.12,<3.15`)
- [uv](https://docs.astral.sh/uv/)
- A Responses-compatible model endpoint, model name, and API credential for Agent operation

The runtime uses the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) Responses model adapter. Fresh source installations have no preselected provider. Web Setup accepts the initial model connection and credential; Settings allows the Main, Title, Activity, Permission and Compact roles to use independent configurations.

The installation commands below select Python 3.12.

## Installation

### Source checkout

```bash
git clone --branch v0.2.0 https://github.com/huangyicong0769/investorch-agent.git
cd investorch-agent
uv sync --locked --python 3.12
```

The examples below use the source-checkout form `uv run --locked investorch`. For a wheel tool installation, omit `uv run --locked` and start each command with `investorch`.

### Release wheel

Download `investorch-0.2.0-py3-none-any.whl`, `investorch-0.2.0.tar.gz` and `SHA256SUMS` from the [0.2.0 GitHub Release](https://github.com/huangyicong0769/investorch-agent/releases/tag/v0.2.0).

From the directory containing the wheel and checksum file:

```bash
# macOS
grep 'investorch-0.2.0-py3-none-any.whl$' SHA256SUMS | shasum -a 256 -c -

# Linux
grep 'investorch-0.2.0-py3-none-any.whl$' SHA256SUMS | sha256sum -c -
```

Then install the wheel as a uv-managed tool:

```bash
uv tool install --python 3.12 ./investorch-0.2.0-py3-none-any.whl
```

The installed command is `investorch`. For an existing tool installation, complete the backup and preflight steps in [Upgrade](docs/Upgrade.md) before replacing it. The published [0.1.0 release](https://github.com/huangyicong0769/investorch-agent/releases/tag/v0.1.0) retains its own [historical instructions](docs/releases/0.1.0.md).

## First run

### 1. Open Web Setup

```bash
uv run --locked investorch web
```

Open <http://127.0.0.1:1334>. A fresh instance keeps serving the Setup page without starting an Agent. Enter the provider endpoint, model and API key, choose the initial role settings, and save. Credentials remain in `~/.investorch/investorch.toml`; the browser does not receive them on subsequent reads.

### 2. Test and start

The optional connection test sends a small real model request and reports its result. Saving configuration alone does not establish connectivity. Complete Setup to start the ApplicationHost and open Chats. Invalid configuration or incompatible existing data opens a recovery state with repair guidance instead of overwriting the instance.

Model endpoints, names, and secret references must be configured explicitly. Missing model settings open Setup; unknown fields or invalid values fail current Schema validation. See [Upgrade](docs/Upgrade.md).

### 3. Start an interface

Subsequent Web starts use the saved settings. The server listens on loopback only; its Python Schema default address is <http://127.0.0.1:1334>:

```bash
uv run --locked investorch web
uv run --locked investorch web --port 8000
```

After model setup, the TUI is an equally supported interface:

```bash
uv run --locked investorch
```

The plain console is available for diagnostics:

```bash
uv run --locked investorch --plain
```

## Portfolio

Portfolio records actual, user-confirmed investment facts in `state/portfolio.db` (or the configured state directory). Agent Tools create and initialize Portfolios, record trades/cash flows/income, append corrections, and transfer positions or cash atomically between Portfolios. Holdings and logical cash replay from the append-only Ledger; historical rows are not edited or deleted. Each Portfolio uses one base currency, exact-decimal arithmetic and explicit unknown cost.

The Web Portfolios pages show metadata, holdings, cash and recent Ledger entries. **New Portfolio** and **Ask Agent** open Agent workflows; economic mutations use the normal approval path. Related Portfolio identities persist with Sessions. These views do not calculate market value, NAV, performance or broker buying power, and recording a trade does not place an order.

See [Portfolio domain](docs/Portfolio_Domain_Model.md), [UI and Session context](docs/Portfolio_UI_and_Session_Context.md), and [schema v6 upgrade](docs/Upgrade.md#portfolio-v1v6).

## Skills

Fresh initialization creates `MEMORY.md` and installs six built-in Skills under `workspace/skills/`: `skill-creator`, `skill-installer`, `investorch-configuration`, `investorch-portfolio`, `rqalpha-strategy`, and `qmt-strategy`. Memory holds user/project context; Skills hold reusable workflows.

```bash
uv run --locked investorch --update
```

Run this explicitly after reviewing managed Skill differences in System Status or `investorch upgrade check`. It deterministically replaces package-managed built-ins, preserves enabled state, and exits without model calls or Portfolio initialization. Package content wins over local edits and versions; customize a built-in by forking it under a new name. External/created Skills and user Memory are untouched.

The Main Agent receives metadata at application startup and loads relevant Skills on demand. Install, replace, enable, disable, remove, and update require an application restart before catalog changes take effect. External/created candidates undergo independent safety review and normal approval; warnings require manual approval and blocked candidates cannot be installed. Installing a Skill does not authorize running its scripts.

QMT guidance creates strategy artifacts for human review and deployment in Big QMT; InvestOrch does not execute QMT orders.

## Image conversations

Web conversations accept JPEG, PNG, GIF and WebP by selection, paste or drop, including image-only messages. Images stay with Session history; `explore` lets the Agent read Workspace images. External images require a click to load. Configure limits under `[images]` and restart to apply them; see the [image architecture](docs/InvestOrch_Agent_Architecture.md#image-conversation-content).

## Session tools

For the unreleased 0.3.0 work, Agents can discover exact Session IDs with `list_sessions`, read history with `read_session`, and submit approved text/images to another unarchived Session in the same Root with `send_message`. For example, `send_message(target_session_id="<exact-id>", text="Inspect this chart", images=[{"source":"workspace","path":"charts/result.png"}])`. Existing uploads can be forwarded by the sender's Journal `seq` and `image_index`, including image-only input. The recipient sees the sender's identity; Web/TUI show it in the usual message view. Read later history or send an explicit reply for results: receipts confirm submission, and Queue does not survive restart. See [Session messaging](docs/InvestOrch_Agent_Architecture.md#session-messaging) for the contract and limits.

## Settings and System Status

- **Settings** edits persistent configuration using the Python Schema for types, defaults, constraints and lifecycle. Reset removes a local override. Hot changes apply to future applicable operations; restart-required changes are saved for the next start. Temporary Run defaults remain process-local.
- **Integrations** manages the separate `mcp.toml` registry. Only `streamable_http` is supported. Hidden URL/header credentials are preserved when left unchanged, and edits require restart. An untested connection is shown as unknown.
- **Skills** lists and inspects installed content. Create/Install and per-Skill Edit/Fork open Agent Sessions with normal review and approval. Operators can directly enable, disable, or confirm removal of custom Skills.
- **System Status** presents the same local checks as the offline CLI:

```bash
uv run --locked investorch doctor
uv run --locked investorch doctor --json
uv run --locked investorch upgrade check
```

Doctor is read-only and does not call model providers, connect MCP servers, update Skills or migrate databases. Exit codes are `0` for healthy/disabled, `1` for warning/unknown, and `2` for error. Follow [Upgrade](docs/Upgrade.md) and [Backup and Recovery](docs/Backup_and_Recovery.md) for offline backups, verification, and recovery into a separate root. Raw credential configuration is excluded from default backups.

## Optional CNEquity integration

Install the optional extra only when using the current CNEquity CLI passthrough, read-only MCP integration, or RQAlpha overlay:

```bash
uv sync --locked --python 3.12 --extra cnequity
```

For a wheel installation, include the extra when installing the tool:

```bash
uv tool install --python 3.12 './investorch-0.2.0-py3-none-any.whl[cnequity]'
```

CNEquity is an optional data backend. Broader integration is deferred until its upstream behavior is sufficiently stable.

## Data maintenance

### RQAlpha bundle

Native RQAlpha backtests read the bundle configured by `backtest.rqalpha_bundle_dir`. The Python Schema default resolves it to `~/.investorch/.rqalpha/bundle`. RQAlpha's `-d` option takes the parent directory and appends `bundle` itself, so the matching CLI path is `~/.investorch/.rqalpha`.

Download RQAlpha's monthly bundle and check it before running a backtest:

```bash
uv run --locked rqalpha download-bundle -d ~/.investorch/.rqalpha
uv run --locked rqalpha check-bundle -d ~/.investorch/.rqalpha
```

RQDatac users can update the same directory with `rqalpha update-bundle`; run `uv run --locked rqalpha update-bundle --help` for its connection and concurrency options.

### CNEquity lake

`investorch data` forwards the remaining arguments to the installed [CNEquity CLI](https://rootsunc.github.io/cnequity/) and runs it from the InvestOrch root. Its default configuration path therefore resolves to `~/.investorch/configs/cnequity.toml`.

Bootstrap and validate the data configuration:

```bash
uv run --locked investorch data config init
uv run --locked investorch data config validate
uv run --locked investorch data doctor
```

Create the initial lake. The `quick` profile fetches the latest three years for every symbol; use `--profile full` when full history is required:

```bash
uv run --locked investorch data init --profile quick
```

Run routine maintenance and inspect data health:

```bash
uv run --locked investorch data run daily
uv run --locked investorch data status --datasets
uv run --locked investorch data verify
uv run --locked investorch data audit --full
```

Preview removable staging and snapshots before cleanup:

```bash
uv run --locked investorch data clean --dry-run
```

Use `uv run --locked investorch data --help` and `<command> --help` for backfill, retry, compaction, source probes, catalog, statistics, query, and other upstream operations.

## Local-first boundary

Configuration, workspace files, session metadata, journals, logs, and generated artifacts are stored locally by default. Configured model endpoints and MCP servers receive the content required for their calls. The Web server listens on loopback only. HTTP writes enforce the local Host, same-origin browser requests and JSON content; this does not provide authenticated LAN serving.

## Frontend development

Run the Python Web server, then start Vite in another terminal:

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

- [0.2.0 release notes](docs/releases/0.2.0.md) and [Changelog](CHANGELOG.md).
- [Upgrade](docs/Upgrade.md) and [Backup and Recovery](docs/Backup_and_Recovery.md).
- [Architecture](docs/InvestOrch_Agent_Architecture.md): the implemented system and current boundaries.
- [Runtime / Session Execution Model](docs/Runtime_Session_Execution_Model.md): lifecycle and concurrency rules.
- [Product Roadmap](docs/InvestOrch_Agent_Product_Roadmap.md): confirmed directions, open decisions, and candidate themes.

## Investment disclaimer

InvestOrch Agent is a personal research and software project. It does not provide investment, legal, tax, or accounting advice, and it does not recommend or solicit any security or transaction.

Market data, third-party data, model output, calculations, and backtests may be inaccurate, incomplete, delayed, or affected by assumptions and hindsight. Historical and backtested performance does not indicate future results.

Independently verify data and outputs before acting. You are responsible for investment decisions, credentials, configuration, regulatory compliance, and any resulting gains or losses. Test with non-production accounts and environments before connecting real capital.
