# QMT Agent Trader

QMT Agent Trader is a local agent application with Web, Textual, and plain-console clients over one shared runtime.

## Run the Web interface

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync
uv run qmt-agent web
```

The first run initializes local state in `~/.qmt-agent-trader` and exits. Add the required local secrets to `~/.qmt-agent-trader/qmt.toml` (the bundled defaults currently reference `DEEPSEEK_API_KEY`), then run the Web command again.

Open <http://127.0.0.1:1334>. To use another loopback port:

```bash
uv run qmt-agent web --port 8000
```

The wheel contains the application defaults, bootstrap templates, and production Web bundle, so a source checkout and Node.js are not needed at runtime. The 0.1 server intentionally listens on loopback only and does not expose an unauthenticated LAN endpoint.

## Other clients

```bash
uv run qmt-agent
uv run qmt-agent --plain
```

## Frontend development

Run the Python Web server on its default port, then start Vite in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Vite proxies `/api` and `/ws` to `http://127.0.0.1:1334`. Before packaging a frontend change, run:

```bash
npm run typecheck
npm run lint
npm run build
```

`npm run build` replaces `src/qmt_agent/web/static` with the production bundle. Build the Python wheel with:

```bash
uv build
```
