# Third-party notices

InvestOrch Agent is licensed under the [Apache License 2.0](LICENSE). That license covers InvestOrch Agent's own code and documentation only. Third-party software, services, market data, and other external content retain their respective terms.

## Python runtime dependencies

The Python distribution declares, but does not bundle, its runtime dependencies. Their own distributions carry their complete license texts.

| Project | Role | License |
| --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi) | Web application framework | MIT |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | Agent runtime, Tools, sessions, HITL, and MCP integration | MIT |
| [RQAlpha](https://github.com/ricequant/rqalpha) | Backtesting engine | Project-specific license described below |
| [RQRisk](https://github.com/ricequant/rqrisk) | Risk calculations installed through RQAlpha | Project-specific license described below |
| [socksio](https://github.com/sethmlarson/socksio) | SOCKS protocol support | MIT |
| [Textual](https://github.com/Textualize/textual) | TUI framework | MIT |
| [TomlKit](https://github.com/python-poetry/tomlkit) | TOML parsing and editing | MIT |
| [Uvicorn](https://github.com/Kludex/uvicorn) | ASGI server | BSD-3-Clause |

The optional `cnequity` extra installs [CNEquity](https://github.com/rootSunc/cnequity), which is licensed under Apache-2.0.

### RQAlpha and RQRisk restrictions

RQAlpha's repository license is not an unmodified Apache-2.0 license. It permits the defined non-commercial uses under Apache-2.0 conditions and requires separate authorization from Ricequant for the commercial uses described in that license. See the complete [RQAlpha license](https://github.com/ricequant/rqalpha/blob/master/LICENSE) before using or distributing an installation that includes RQAlpha.

RQRisk is installed as a dependency of RQAlpha. Its distribution includes a separate license with the same non-commercial and commercial-use distinction. See the complete [RQRisk license](https://github.com/ricequant/rqrisk/blob/master/LICENSE.txt).

InvestOrch Agent's Apache-2.0 license does not replace, broaden, or override either license.

## Bundled Web client

The built Web client contains JavaScript and CSS produced from npm dependencies under permissive licenses including MIT, Apache-2.0, ISC, and 0BSD. The complete package-by-package notices and license texts are distributed with the Web client as `THIRD_PARTY_LICENSES.txt` and are regenerated from `frontend/package-lock.json` during each production build.

## External services and data

DeepSeek and any other configured model endpoint, MCP server, market-data source, or broker service are external to this repository. Their service terms and data licenses apply independently.
