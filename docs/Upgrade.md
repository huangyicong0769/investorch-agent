# Upgrade and preflight

[简体中文](Upgrade.zh-CN.md)

This guide covers the 0.3.0 release candidate's model transition and retains the earlier 0.2.0 upgrade procedure. 0.3.0 publication is pending; use a reviewed local checkout or built wheel as described in the [README](../README.md#installation). See [0.3.0 release notes](releases/0.3.0.md).

## 0.2.0 → 0.3.0 model configuration (unreleased)

0.3.0 makes a **breaking TOML change**. There is no `config_version` and no automatic TOML conversion. The five old `[models.<role>]` tables split into named connections and fixed Agent defaults:

| Old effective field | New field |
| --- | --- |
| `models.<role>.base_url`, `api_key_secret` | `models.<connection>.base_url`, `api_key_secret`, with `type = "openai_compatible"` |
| `models.<role>.name`, `reasoning_effort`, `context_window_tokens` | `agents.<role>` fields of the same names |
| Role-to-connection relationship | `agents.<role>.model = "<connection>"` |

Use the **effective old configuration**, not just fields present in Local TOML: 0.2.0 overrides Python defaults, so omitted fields may still have been active. Preserve separate endpoints or credentials as separate connections. Reuse a connection only when the endpoint and authentication identity agree. Each effective model needs a positive window; auxiliary inheritance is limited to the same connection and actual model. Examples and account setup are in [Configuration](Configuration.md).

### Prepare with the old working installation

1. Record Session IDs/titles, Portfolio IDs and Ledger counts, Skill enabled state and important Workspace hashes. Stop every InvestOrch writer, including Web/TUI/plain and managed jobs that can write the Root.
2. Run the old installation's offline checks and create a private, complete backup outside the Root. Keep the prior package/environment. The ZIP with sensitive configuration complements a full stopped-Root copy, because normal backups omit logs, market-data bundles and other documented exclusions.

```sh
# Run these with the existing 0.2.0 installation, before replacing it.
investorch doctor
investorch upgrade check --json
umask 077
instance_root="$HOME/.investorch"
backup_dir="$HOME/investorch-private-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
cp -a "$instance_root" "$backup_dir/root"
investorch backup create --root "$instance_root" --output "$backup_dir/state.zip" --include-sensitive-config
investorch backup verify "$backup_dir/state.zip"
```

These copies contain plaintext credentials and private history; keep them on private local storage. If the old CLI cannot read the configuration, retain the full stopped-Root copy before repair. Follow [Backup and Recovery](Backup_and_Recovery.md) for exclusions and archive restoration.

3. With the old working source/environment, record each role's effective name, endpoint, secret **reference**, effort and window in a private local note. Do not print or export secret values. For a 0.2.0 source checkout, this writes only those model settings to the private backup:

```sh
uv run --project /path/to/old-0.2.0-checkout --locked python - "$instance_root" "$backup_dir/model-settings.json" <<'PY'
import json
import sys
from dataclasses import asdict
from pathlib import Path
from investorch.config import load_config
config = load_config(root=Path(sys.argv[1]))
roles = ("main", "title", "activity", "permission", "compact")
target = Path(sys.argv[2])
with target.open("x", encoding="utf-8") as output:
    json.dump({role: asdict(config.model(role)) for role in roles}, output, indent=2)
target.chmod(0o600)
PY
```

### Convert and validate offline

4. After reviewing the intended changes, install the chosen 0.3.0 development build in its own environment. Edit the private `investorch.toml` while writers remain stopped: replace old role tables with the two layers, preserve non-model settings and existing API-key values, and set every required effective window. Never copy this TOML into the repository or a report. ChatGPT credential records come from the browser authorization flow after structural conversion.
5. Validate with the new build without starting the Host. For an isolated rehearsal, supply a copied Root in place of the real Root:

```sh
uv run --project /path/to/0.3.0-checkout --locked python - "$instance_root" <<'PY'
import sys
from pathlib import Path
from investorch.config import load_config
config = load_config(root=Path(sys.argv[1]))
print("Missing setup fields:", config.readiness())
PY
uv run --project /path/to/0.3.0-checkout --locked investorch doctor --root "$instance_root"
uv run --project /path/to/0.3.0-checkout --locked investorch upgrade check --root "$instance_root" --json
```

6. Start one interface with the new build, complete any ChatGPT authorization, and test each configured role. Compare the inventory from step 1. Check Web/TUI Session models, resume, Fork and a new Run; verify persisted selections survive restart while an unused Draft does not. Use `--update` only when ready to replace managed built-in Skills, then restart.
7. On failure, stop the new writers and restore the **matching complete pre-upgrade state and old environment**. Do not put only the old TOML over upgraded databases or open those databases with the old package. Retain the failed Root separately until diagnosis is complete.

After stopping all writers, restore the retained full copy with the old environment:

```sh
# Reuse the exact backup_dir selected above; do not choose a new timestamp here.
failed_root="${instance_root}.failed-$(date +%Y%m%d-%H%M%S)"
mv "$instance_root" "$failed_root"
cp -a "$backup_dir/root" "$instance_root"
uv run --project /path/to/old-0.2.0-checkout --locked investorch doctor --root "$instance_root"
uv run --project /path/to/old-0.2.0-checkout --locked investorch upgrade check --root "$instance_root" --json
```

Session model metadata uses the normal additive SQLite initialization: old rows may have no model binding and receive one on their first actual Run. Empty Sessions also need no persisted model until a Run starts. This database upgrade preserves Session identity, Portfolio and Journal; it is separate from the manual TOML conversion. Portfolio migration requirements below still apply independently.

Offline checks establish configuration structure and credential presence, not live ChatGPT entitlement or token validity. Connection definitions and Agent defaults apply after restart; OAuth login/logout/refresh apply immediately. No local migration is implied by installing the package or reading this guide: perform it only after reviewing the code and the private migration changes.

## Earlier 0.2.0 upgrade procedure

Use Python 3.12–3.14 (`>=3.12,<3.15`). The commands below select Python 3.12 explicitly.

## Choose the preflight command

`doctor`, `upgrade check` and `backup` are new in 0.2.0. When upgrading a 0.1.0 tool installation, leave the previous tool installed and run the 0.2.0 offline commands from a [0.2.0 source checkout](releases/0.2.0.md#source-checkout):

```sh
uv run --project /path/to/investorch-agent --locked --python 3.12 investorch doctor --root "$HOME/.investorch"
uv run --project /path/to/investorch-agent --locked --python 3.12 investorch upgrade check --root "$HOME/.investorch" --json
```

For an already installed 0.2.0 version, run:

```sh
investorch doctor
investorch upgrade check --json
```

Both commands are offline and read-only. They do not create an ApplicationHost, connect providers/MCP, initialize storage, rewrite configuration or update Skills. Use `--root /path/to/root` to inspect a separate installation. Exit status is `0` for healthy/disabled checks, `1` for warnings or unknown results, and `2` for errors. Inspect each check's remediation; an untested connection or missing optional data is not proof of corruption.

## Recommended sequence

1. Inspect preflight results and stop all clients/writers. Preserve a record of Session IDs/titles, Portfolio IDs/Ledger counts/holdings/cash, Skill enabled state, and important Workspace files for comparison.
2. Create and verify an [offline backup](Backup_and_Recovery.md) using the 0.2.0 CLI. The output directory must exist and the archive must be outside the Root. Keep the archive and previous package until the upgraded state has been checked. If configuration validation prevents backup creation, first make a complete offline copy of the stopped Root, including all SQLite sidecars, before repairing configuration. That copy includes raw credentials and must be kept private.
3. Install the checksum-verified 0.2.0 package using your existing installation method. For a uv tool installation, use `uv tool install --python 3.12 --force ./investorch-0.2.0-py3-none-any.whl`; retain `[cnequity]` when replacing a tool that uses that extra. For a source installation, check out `v0.2.0` and run `uv sync --locked --python 3.12` with the same extras. Package installation does not itself migrate application data.
4. Resolve any explicit Portfolio migration requirement below.
5. Run `investorch --update` when you want the new package's managed built-in Skill contents, then restart.
6. Run Doctor again, open Web/TUI/plain, and compare the records captured in step 1. Verify the configured model connection and MCP/data integrations separately; offline checks and archive verification do not prove external connectivity.

In a source checkout, prefix commands with `uv run --locked`.

## Local configuration

Python Schema supplies defaults; `~/.investorch/investorch.toml` supplies user overrides and secrets. Every configuration is validated against the current Schema. Model endpoints, names, and secret references must be configured explicitly; the application does not restore historical provider defaults or migrate Local TOML.

Invalid TOML, unknown fields, and invalid values must be repaired or restored from a known-good backup. Do not delete the root or its databases to make startup succeed. Missing model configuration or credentials can be completed through `investorch web`; TUI/plain points to Setup.

In particular, a 0.1.0 installation may have relied on packaged DeepSeek model defaults while its Local TOML stored only the key. Configure all required model roles explicitly for 0.2.0; saving a credential alone does not restore those old defaults. If Web reports recovery for malformed configuration, repair the file offline before restarting; Setup handles missing model settings, not arbitrary schema errors.

Changes to storage/Workspace directories require an offline move with clients stopped; Web rejects these path edits. Keep the saved directory layout consistent with the restored data. Credentials and other restart-required settings use the saved value only after restarting.

## Portfolio v1–v6

| Existing schema | Behavior |
| --- | --- |
| v1 or v2 | Normal initialization uses the existing backed-up migration to v6. Explicit migration is also available. |
| v3, v4 or v5 | Requires explicit legacy-live retirement and migration; normal startup refuses automatic retirement. |
| v6 | Validated and reused. |
| Unknown version or damaged database | Fails without replacing the database. Keep the original and investigate or restore. |

```sh
investorch migrate portfolio --dry-run
investorch migrate portfolio
# A separate database:
investorch migrate portfolio --database /path/to/portfolio.db --dry-run
```

Dry-run creates no archive and performs no writes. WAL databases are rejected because SQLite read-only access can modify sidecars: stop writers and use the owning application to checkpoint and switch to `journal_mode=DELETE` before repeating dry-run. Doctor/Preflight reports this as Unknown. Offline backup can preserve committed WAL rows without checkpointing the source.

Legacy `PREPARED`/`ACTIVE` live deployments block migration. Use the old version to stop and clean them, including the old QMT execution node or automated strategies; then rerun preflight. Terminal legacy state is archived before explicit migration removes legacy tables. Existing archives and ledger facts are retained. The supported QMT route remains Agent-authored strategy artifacts followed by human review/deployment.

## Explicit built-in Skill updates

The 0.1.0 `--sync` and `--sync-force` flags are removed. `--update` updates managed Skills only; it does not merge or replace user Memory or old product-guide files.

```sh
investorch --update
```

This replaces managed built-in contents with the installed package's copies, preserves enabled state and custom Skills, and reports installed/updated/unchanged counts. It does not initialize or migrate Session/Portfolio databases, or rewrite Local TOML. In a new root it creates only the directories and Skill registry needed for the update. Normal startup never updates installed built-in contents automatically.

Fork built-ins under a new name for customization. Doctor reports missing/invalid Skills and content drift, including changes whose version string stayed the same. Restart after management changes: opening a new Session in the same process does not refresh the startup catalog.

If a pre-existing custom/unregistered Skill uses a new built-in name, initialization/update stops rather than overwriting it. Preserve and rename or move that Skill before retrying.

## Roll back an upgrade

Stop all writers. Restore a verified pre-upgrade backup into a separate empty Root following [Backup and Recovery](Backup_and_Recovery.md), then reinstall the retained prior package. A default backup requires re-entering excluded credentials and MCP settings. Do not point an older package at upgraded storage or replace individual SQLite files while the application is running. Compare the restored records before using the restored Root as `~/.investorch`.
