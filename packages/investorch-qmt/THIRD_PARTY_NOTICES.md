# Third-party notices

InvestOrch QMT is licensed under the [Apache License 2.0](LICENSE). That license covers InvestOrch QMT's own code and documentation only. Third-party software retains its respective terms.

The Python distribution declares, but does not bundle, its runtime dependencies. Their own distributions carry their complete license texts.

| Project | Role | License |
| --- | --- | --- |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | Model Context Protocol server and client | MIT |
| [mcp-types](https://github.com/modelcontextprotocol/python-sdk) | Independently distributed MCP wire types | MIT |
| [RQAlpha](https://github.com/ricequant/rqalpha) | RQAlpha runtime foundation | Project-specific license described below |
| [RQRisk](https://github.com/ricequant/rqrisk) | Risk calculations installed through RQAlpha | Project-specific license described below |
| [Platformdirs](https://github.com/tox-dev/platformdirs) | Windows local application path resolution | MIT |
| [Starlette](https://github.com/Kludex/starlette) | ASGI application primitives | BSD-3-Clause |
| [TomlKit](https://github.com/python-poetry/tomlkit) | TOML parsing and editing | MIT |
| [Uvicorn](https://github.com/Kludex/uvicorn) | ASGI server | BSD-3-Clause |

### RQAlpha and RQRisk restrictions

RQAlpha's repository license is not an unmodified Apache-2.0 license. It permits the defined non-commercial uses under Apache-2.0 conditions and requires separate authorization from Ricequant for the commercial uses described in that license. See the complete [RQAlpha license](https://github.com/ricequant/rqalpha/blob/master/LICENSE) before using or distributing an installation that includes RQAlpha.

RQRisk is installed as a dependency of RQAlpha. Its distribution includes a separate license with the same non-commercial and commercial-use distinction. See the complete [RQRisk license](https://github.com/ricequant/rqrisk/blob/master/LICENSE.txt).

InvestOrch QMT's Apache-2.0 license does not replace, broaden, or override either license.
