# Upgrade and preflight

[简体中文](Upgrade.zh-CN.md)

This guide covers upgrading to 0.2.0. See the [release notes](releases/0.2.0.md) for scope and package names.

Use Python 3.12–3.14 (`>=3.12,<3.15`). The commands below select Python 3.12 explicitly.

## Choose the preflight command

`doctor`, `upgrade check` and `backup` are new in 0.2.0. When upgrading a 0.1.0 tool installation, leave the previous tool installed and run the 0.2.0 offline commands from a [source checkout](../README.md#source-checkout):

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
