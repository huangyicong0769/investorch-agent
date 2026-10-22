# Configuration one-shot example

User request: “Persist the Agent turn limit as 120 and image detail as high. Tell me what is effective now and what needs a restart.”

Inputs: the user has selected the intended running InvestOrch instance and authorized these two normal settings. No model, credential, or permission changes are requested. The tool calls below are InvestOrch Agent calls, not shell commands or Codex tool calls. Every return is **illustrative example JSON**, not evidence that a real instance was changed. The normal mutation approval applies when these tools execute.

For a backtest request such as “use a different commission for this comparison,” follow `rqalpha-strategy` and pass `run_backtest.parameters`. Its null fields inherit application defaults; false and zero explicitly override them. The configuration mutation sequence below applies when the user requests application settings or defaults.

## Load the domain instructions

```json
{
  "tool": "load_skill",
  "arguments": {
    "name": "investorch-configuration"
  }
}
```
Selected fields of the illustrative return (the actual return also includes full Skill content and description):

```json
{
  "name": "investorch-configuration",
  "version": "1.3.1",
  "resources": [
    "references/configuration.md",
    "references/examples.md"
  ]
}
```
The resource inventory contains `references/configuration.md`. Installed Skills live under Workspace `skills/<name>`, so read that resource at the corresponding Workspace-relative path before proceeding:

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "read",
    "path": "skills/investorch-configuration/references/configuration.md",
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
  "path": "skills/investorch-configuration/references/configuration.md"
}
```

## Inspect the exact effective keys

```json
{
  "tool": "get_config",
  "arguments": {
    "key": "runtime.max_turns"
  }
}
```
```json
{
  "key": "runtime.max_turns",
  "value": 100
}
```
```json
{
  "tool": "get_config",
  "arguments": {
    "key": "images.default_detail"
  }
}
```
```json
{
  "key": "images.default_detail",
  "value": "auto"
}
```

Both differ from the requested values. The configuration reference classifies `runtime.max_turns` as hot and `images.default_detail` as restart-required. Preserve integer and string types. Use persistence for both because the user requested saved settings.

## Apply through the configuration tool

```json
{
  "tool": "update_config",
  "arguments": {
    "key": "runtime.max_turns",
    "value": 120,
    "persist": true
  }
}
```
```json
{
  "key": "runtime.max_turns",
  "value": 120,
  "persisted": true,
  "applied": true,
  "requires_restart": false
}
```
```json
{
  "tool": "update_config",
  "arguments": {
    "key": "images.default_detail",
    "value": "high",
    "persist": true
  }
}
```
```json
{
  "key": "images.default_detail",
  "value": "high",
  "persisted": true,
  "applied": false,
  "requires_restart": true
}
```

The second response confirms a saved next-start value, not an effective runtime change. If an approval is denied or the tool returns an error, stop that mutation branch and report the actual outcome; a request alone proves no change.

## Read back and report the lifecycle

```json
{
  "tool": "get_config",
  "arguments": {
    "key": "runtime.max_turns"
  }
}
```
```json
{
  "key": "runtime.max_turns",
  "value": 120
}
```
```json
{
  "tool": "get_config",
  "arguments": {
    "key": "images.default_detail"
  }
}
```
```json
{
  "key": "images.default_detail",
  "value": "auto"
}
```

The hot key readback matches 120. The image readback still shows auto, consistent with `applied=false`. Persistence evidence is the successful tool response; `get_config` reads effective values and is not a saved-override inspector. Do not use shell access to the host configuration to bypass this lifecycle.

Final answer example: “The turn limit is saved as 120 and reads back as effective now. Image detail is saved as high, while the running process still reports auto. An application restart is required for high; I have not restarted it.”

If later asked to verify adoption after the operator restarts, read `images.default_detail` again and report success only if it returns high. New Session creation alone does not restart the application.
