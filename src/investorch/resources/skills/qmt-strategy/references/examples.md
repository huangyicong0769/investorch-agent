# QMT one-shot example: historical observation artifact

User request: “Write a Big QMT built-in Python observation script for 600000.SH. From six completed daily closes ending 2025-12-31, report five-bar momentum, with no adjustment or data filling. Use local downloaded data and never submit orders. Validate the calculation locally and give me the file for manual client review.”

Inputs: the target is Big QMT built-in Python with `init`/`handlebar` and the documented `get_market_data_ex` API, not MiniQMT. The operator will check their exact broker build and embedded Python/dependencies. The Workspace has no `strategies` directory yet, and local shell access has uv and Python 3.8 or later available. All data are at a fixed historical cutoff; this is a one-snapshot observer, not a rolling signal or performance backtest. The operator must download local daily data through the cutoff in QMT. No account, credentials, connectivity, or fills are assumed.

The platform reference establishes the client/data boundary. The market-data call and symbol-keyed DataFrame return used below are also described in the [official built-in Python market-data reference](https://dict.thinktrader.net/innerApi/data_function.html). Confirm API availability in the actual client before deployment.

The calls below are InvestOrch Agent tools; every returned object is **illustrative example JSON**, not a real QMT execution or local test record.

## Load the domain instructions

```json
{
  "tool": "load_skill",
  "arguments": {
    "name": "qmt-strategy"
  }
}
```
Selected fields of the illustrative return (the actual return also includes full Skill content and description):

```json
{
  "name": "qmt-strategy",
  "version": "1.1.0",
  "resources": [
    "references/big-qmt.md",
    "references/examples.md"
  ]
}
```
The resource inventory contains `references/big-qmt.md`. Installed Skills live under Workspace `skills/<name>`, so read that resource at the corresponding Workspace-relative path before proceeding:

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "read",
    "path": "skills/qmt-strategy/references/big-qmt.md",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```
Selected fields of the illustrative read return; read its full `content` (the reference shipped alongside this example) before choosing the toolchain:

```json
{
  "type": "file",
  "path": "skills/qmt-strategy/references/big-qmt.md"
}
```

## Discover the artifact location and write complete source

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "list",
    "path": ".",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```
```json
{
  "type": "directory",
  "path": ".",
  "entries": [
    {"name": "skills", "type": "directory"}
  ]
}
```

The listing confirms no `strategies` path; create a new artifact there. If a file already exists, read it first and use a deliberate replacement or a distinct path. The source contains no order call. Its process-local flag suppresses repeated successful observation output only; restarting intentionally repeats the observation.

```json
{
  "tool": "edit",
  "arguments": {
    "path": "strategies/qmt_observation.py",
    "operation": "create",
    "content": "# Observation only: Big QMT built-in Python, no order functions.\nimport math\n\nVERSION = \"observation-1\"\nSYMBOL = \"600000.SH\"\nCUTOFF = \"20251231\"\n\n\ndef five_bar_momentum(closes):\n    if len(closes) != 6:\n        raise ValueError(\"need exactly six completed daily closes\")\n    values = [float(value) for value in closes]\n    if not all(math.isfinite(value) and value > 0 for value in values):\n        raise ValueError(\"closes must be finite and positive\")\n    return values[-1] / values[0] - 1.0\n\n\ndef init(ContextInfo):\n    ContextInfo.observation_done = False\n\n\ndef handlebar(ContextInfo):\n    if ContextInfo.observation_done:\n        return\n    data = ContextInfo.get_market_data_ex(\n        fields=[\"close\"], stock_code=[SYMBOL], period=\"1d\",\n        start_time=\"\", end_time=CUTOFF, count=6,\n        dividend_type=\"none\", fill_data=False, subscribe=False)\n    frame = data.get(SYMBOL)\n    if frame is None or len(frame) != 6 or \"close\" not in frame:\n        print(VERSION, SYMBOL, CUTOFF, \"SKIP: insufficient local history\")\n        return\n    try:\n        momentum = five_bar_momentum(frame[\"close\"].tolist())\n    except (TypeError, ValueError, OverflowError) as error:\n        print(VERSION, SYMBOL, CUTOFF, \"SKIP:\", str(error))\n        return\n    print(VERSION, SYMBOL, CUTOFF, \"last_bar\", str(frame.index[-1]),\n          \"last_close\", float(frame[\"close\"].iloc[-1]),\n          \"five_bar_momentum\", momentum, \"OBSERVE_ONLY\")\n    ContextInfo.observation_done = True\n",
    "old_text": ""
  }
}
```
```json
{
  "path": "strategies/qmt_observation.py",
  "operation": "create",
  "size": 1470
}
```

Use the returned Workspace-relative path for validation and readback. This illustrative return gives `strategies/qmt_observation.py`, so the following command uses that path. The data request has a nonempty fixed `end_time`, `count=6`, `dividend_type="none"`, `fill_data=False`, and `subscribe=False`. A missing/invalid history prints SKIP, never a buy or sell intent.

## Validate syntax and pure math locally

```json
{
  "tool": "exec_command",
  "arguments": {
    "command": "uv run --no-project python - <<'PY'\nimport ast\nimport json\nimport math\nimport runpy\nfrom pathlib import Path\n\npath = Path(\"strategies/qmt_observation.py\")\nsource = path.read_text(encoding=\"utf-8\")\nast.parse(source, filename=str(path), feature_version=(3, 8))\ncompile(source, str(path), \"exec\")\nsignal = runpy.run_path(str(path))[\"five_bar_momentum\"]\nassert math.isclose(signal([100, 101, 102, 103, 104, 110]), 0.1)\nassert math.isclose(signal([100, 99, 98, 97, 96, 90]), -0.1)\nassert signal([100] * 6) == 0.0\nfor invalid in ([100] * 5, [100] * 7, [0] * 6, [float(\"nan\")] * 6, [float(\"inf\")] * 6):\n    try:\n        signal(invalid)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError(\"invalid fixture accepted\")\nprint(json.dumps({\"path\": str(path), \"syntax\": \"pass\", \"pure_math_cases\": 8, \"qmt_runtime\": \"not_executed\"}))\nPY",
    "background": false,
    "timeout_seconds": 30
  }
}
```
```json
{
  "stdout": "{\"path\": \"strategies/qmt_observation.py\", \"syntax\": \"pass\", \"pure_math_cases\": 8, \"qmt_runtime\": \"not_executed\"}\n",
  "stderr": "",
  "exit_code": 0
}
```

Proceed to delivery only after exit code zero and the expected JSON report. The eight fixtures cover rising, falling, flat, short, long, zero, NaN, and infinite inputs. Parsing for Python 3.8 and compiling on the local interpreter is partial compatibility evidence. `runpy` loads definitions; it does not call QMT lifecycle functions or fabricate a QMT connection.

## Read back the artifact

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "read",
    "path": "strategies/qmt_observation.py",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```
```json
{
  "type": "file",
  "path": "strategies/qmt_observation.py",
  "total_lines": 41,
  "start_line": 1,
  "end_line": 41,
  "content": "# Observation only: Big QMT built-in Python, no order functions.\nimport math\n\nVERSION = \"observation-1\"\nSYMBOL = \"600000.SH\"\nCUTOFF = \"20251231\"\n\n\ndef five_bar_momentum(closes):\n    if len(closes) != 6:\n        raise ValueError(\"need exactly six completed daily closes\")\n    values = [float(value) for value in closes]\n    if not all(math.isfinite(value) and value > 0 for value in values):\n        raise ValueError(\"closes must be finite and positive\")\n    return values[-1] / values[0] - 1.0\n\n\ndef init(ContextInfo):\n    ContextInfo.observation_done = False\n\n\ndef handlebar(ContextInfo):\n    if ContextInfo.observation_done:\n        return\n    data = ContextInfo.get_market_data_ex(\n        fields=[\"close\"], stock_code=[SYMBOL], period=\"1d\",\n        start_time=\"\", end_time=CUTOFF, count=6,\n        dividend_type=\"none\", fill_data=False, subscribe=False)\n    frame = data.get(SYMBOL)\n    if frame is None or len(frame) != 6 or \"close\" not in frame:\n        print(VERSION, SYMBOL, CUTOFF, \"SKIP: insufficient local history\")\n        return\n    try:\n        momentum = five_bar_momentum(frame[\"close\"].tolist())\n    except (TypeError, ValueError, OverflowError) as error:\n        print(VERSION, SYMBOL, CUTOFF, \"SKIP:\", str(error))\n        return\n    print(VERSION, SYMBOL, CUTOFF, \"last_bar\", str(frame.index[-1]),\n          \"last_close\", float(frame[\"close\"].iloc[-1]),\n          \"five_bar_momentum\", momentum, \"OBSERVE_ONLY\")\n    ContextInfo.observation_done = True\n"
}
```

Compare the returned content with the intended source: fixed symbol/cutoff, six closes, no filling/adjustment/subscription, explicit invalid-data handling, and observation-only output. The path returned by readback is the delivered artifact. It has not been deployed.

Manual client handoff:

1. The human checks the exact Big QMT build and Python environment, copies the reviewed file into its built-in strategy editor, and selects a 600000.SH daily chart with a date at or after the cutoff.
2. Download and inspect six completed daily bars ending at the cutoff. Verify the last returned bar timestamp is the intended final trading day; six rows alone do not prove freshness. Confirm the client supports the stated data arguments.
3. Start the observer manually. Inspect VERSION, symbol, cutoff, last-bar timestamp, last close, and momentum output. Missing history should produce SKIP. Callback or DataFrame compatibility failures require client-specific repair and revalidation.
4. Stop it manually. This artifact has no trading activation setting or order implementation. Any later executable strategy needs a separate timing, account, order-unit, duplicate/restart, and reconciliation design.

Final answer example: “Created strategies/qmt_observation.py and read it back. Local syntax validation and eight pure-math cases passed, including insufficient and invalid data. The file observes a fixed six-close snapshot through 2025-12-31 and contains no order submission. QMT callbacks, local data coverage, and broker-build compatibility remain for manual client validation; I have not connected to QMT, deployed the script, or executed trades.”
