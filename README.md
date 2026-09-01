# InvestOrch Agent

[简体中文](README.zh-CN.md)

InvestOrch Agent is a local-first, human-in-the-loop investment orchestration agent for an individual investor. Its long-term goal is to support the full path from research, strategy development, and backtesting to portfolio decisions, trading, position monitoring, and review.

Version 0.1.0 is an early preview of that product direction. It is not yet an end-to-end trading system.

## What 0.1.0 includes

- First-class Web and [Textual](https://github.com/Textualize/textual) TUI clients backed by the same application and runtime.
- Persistent sessions with titles, archive and restore, stable-head fork, clear, and context compaction.
- Concurrent runs across sessions, with per-run Steer or Queue follow-up behavior.
- Human approval in manual or model-assisted review mode.
- Append-only JSONL conversation journals and SQLite model-continuation state.
- Workspace-scoped exploration, editing, deletion, calculation, foreground/background command execution, Todo state, configuration, and MCP registry tools.
- Daily stock backtesting with [RQAlpha 6.3.0](https://github.com/ricequant/rqalpha), reproducibility metadata, and workspace-relative artifacts.
- An optional [CNEquity](https://github.com/rootSunc/cnequity) extra for the existing RQAlpha data overlay and read-only MCP integration.

Portfolio/account access, a QMT gateway, live trading, a unified investment data layer, and Multi-Agent orchestration remain future work. See the [Product Roadmap](docs/InvestOrch_Agent_Product_Roadmap.md) for the next directions.

## Status and compatibility

0.1.0 is an early preview. The CLI, configuration, storage formats, and Tool interfaces may change in later 0.x releases.

The core is currently validated on macOS locally and Ubuntu in CI, and targets macOS and Linux environments. Future QMT connectivity will use a gateway running with QMT on Windows.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- A [DeepSeek API key](https://platform.deepseek.com/api_keys) for the bundled model configuration

The runtime uses the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) Responses model adapter. The bundled configuration uses [DeepSeek](https://api-docs.deepseek.com/) by default. All bundled Agent roles read the API key from the local secret named `DEEPSEEK_API_KEY`.

## Installation

### GitHub Release

Download `investorch-0.1.0-py3-none-any.whl` and `SHA256SUMS` from the [`v0.1.0` GitHub Release](https://github.com/huangyicong0769/investorch-agent/releases/tag/v0.1.0). Verify the downloaded wheel from the directory containing both files:

```bash
# macOS
grep 'investorch-0.1.0-py3-none-any.whl$' SHA256SUMS | shasum -a 256 -c -

# Linux
grep 'investorch-0.1.0-py3-none-any.whl$' SHA256SUMS | sha256sum -c -
```

Then install the wheel as a uv-managed tool:

```bash
uv tool install ./investorch-0.1.0-py3-none-any.whl
```

The installed command is `investorch`.

### Source checkout

```bash
git clone https://github.com/huangyicong0769/investorch-agent.git
cd investorch-agent
uv sync --locked
```

The examples below use the source-checkout form `uv run investorch`. When using the release tool installation, omit `uv run` and start each command with `investorch`.

## First run

### 1. Initialize local state

```bash
uv run investorch web
```

The first run creates the local configuration, workspace, and state directories under `~/.investorch`, then exits. This is expected.

### 2. Configure the required API key

Create an API key in the [DeepSeek platform](https://platform.deepseek.com/api_keys). Open `~/.investorch/investorch.toml` with a text editor and add the key below the existing `[secrets]` heading:

```toml
[secrets]
DEEPSEEK_API_KEY = "replace-with-your-api-key"
```

Replace the example value with the real key. Keep the quotation marks and do not share or commit this file. The bundled Main, Title, Activity, Bootstrap, Permission, and Compact Agent configurations all use this secret.

### 3. Start an interface

Run the Web interface again after saving the configuration:

```bash
uv run investorch web
```

The Web client listens on loopback only. Its bundled default address is <http://127.0.0.1:1334>:

```bash
uv run investorch web
uv run investorch web --port 8000
```

The TUI is an equally supported interface:

```bash
uv run investorch
```

The plain console is available for diagnostics:

```bash
uv run investorch --plain
```

## Syncing bootstrap workspace files

Project upgrades may update the bundled templates for `MEMORY.md`, `memory/configuration.md`, and `memory/rqalpha.md`. Merge those updates into the corresponding files under `~/.investorch/workspace`:

```bash
uv run investorch --sync
```

`--sync` uses the model configured in `[models.bootstrap]` to apply current project rules while preserving durable user content. It validates each result and exits after synchronization. When an existing file changes, its previous version is kept under `~/.investorch/state/bootstrap-backups/<timestamp>/`, and the command reports that backup path.

To replace the workspace files directly with the bundled templates without calling a model:

```bash
uv run investorch --sync-force
```

`--sync-force` discards the current contents of those target files. Replaced files are backed up in the same location before replacement.

## Optional CNEquity integration

Install the optional extra only when using the current CNEquity CLI passthrough, read-only MCP integration, or RQAlpha overlay:

```bash
uv sync --locked --extra cnequity
```

For a GitHub Release wheel installation, include the extra when installing the tool:

```bash
uv tool install './investorch-0.1.0-py3-none-any.whl[cnequity]'
```

CNEquity is an optional data backend. Broader integration is deferred until its upstream behavior is sufficiently stable.

## Data maintenance

### RQAlpha bundle

Native RQAlpha backtests read the bundle configured by `backtest.rqalpha_bundle_dir`. The bundled configuration resolves it to `~/.investorch/.rqalpha/bundle`. RQAlpha's `-d` option takes the parent directory and appends `bundle` itself, so the matching CLI path is `~/.investorch/.rqalpha`.

Download RQAlpha's monthly bundle and check it before running a backtest:

```bash
uv run rqalpha download-bundle -d ~/.investorch/.rqalpha
uv run rqalpha check-bundle -d ~/.investorch/.rqalpha
```

RQDatac users can update the same directory with `rqalpha update-bundle`; run `uv run rqalpha update-bundle --help` for its connection and concurrency options.

### CNEquity lake

`investorch data` forwards the remaining arguments to the installed [CNEquity CLI](https://rootsunc.github.io/cnequity/) and runs it from the InvestOrch root. Its default configuration path therefore resolves to `~/.investorch/configs/cnequity.toml`.

Bootstrap and validate the data configuration:

```bash
uv run investorch data config init
uv run investorch data config validate
uv run investorch data doctor
```

Create the initial lake. The `quick` profile fetches the latest three years for every symbol; use `--profile full` when full history is required:

```bash
uv run investorch data init --profile quick
```

Run routine maintenance and inspect data health:

```bash
uv run investorch data run daily
uv run investorch data status --datasets
uv run investorch data verify
uv run investorch data audit --full
```

Preview removable staging and snapshots before cleanup:

```bash
uv run investorch data clean --dry-run
```

Use `uv run investorch data --help` and `<command> --help` for backfill, retry, compaction, source probes, catalog, statistics, query, and other upstream operations.

## Local-first boundary

Configuration, workspace files, session metadata, journals, logs, and generated artifacts are stored locally by default. Configured model endpoints and MCP servers receive the content required for their calls. The Web server listens on loopback only in 0.1.0.

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

InvestOrch Agent's own code and documentation are licensed under [Apache-2.0](LICENSE). Third-party components retain their own licenses. In particular, RQAlpha's repository license limits its defined non-commercial and commercial uses; InvestOrch Agent's license does not override those terms. See [Third-party notices](THIRD_PARTY_NOTICES.md) for details and the bundled Web client license inventory.

## Human-AI development collaboration

InvestOrch Agent is a human-led personal research project developed with AI assistance. A solo developer has limited time, knowledge, and engineering capacity, so AI helps extend the work that can be done in research, design, implementation, and validation. The owner remains deeply involved in product design, investment-domain modeling, architecture, safety boundaries, and key code.

- The owner defines the product direction, makes investment-domain and architecture decisions, writes key code, and performs final review and acceptance.
- AI assists with codebase investigation, design discussion, implementation, testing, and documentation.
- Unclear requirements are discussed before implementation, and accepted changes remain subject to human review.

## Documentation

- [Architecture](docs/InvestOrch_Agent_Architecture.md): the implemented system and current boundaries.
- [Runtime / Session Execution Model](docs/Runtime_Session_Execution_Model.md): lifecycle and concurrency rules.
- [Product Roadmap](docs/InvestOrch_Agent_Product_Roadmap.md): confirmed directions, open decisions, and candidate themes.

## Investment disclaimer

InvestOrch Agent is a personal research and software project. It does not provide investment, legal, tax, or accounting advice, and it does not recommend or solicit any security or transaction.

Market data, third-party data, model output, calculations, and backtests may be inaccurate, incomplete, delayed, or affected by assumptions and hindsight. Historical and backtested performance does not indicate future results.

Independently verify data and outputs before acting. You are responsible for investment decisions, credentials, configuration, regulatory compliance, and any resulting gains or losses. Test with non-production accounts and environments before connecting real capital.
