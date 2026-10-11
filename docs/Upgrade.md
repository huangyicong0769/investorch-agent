# Upgrade and preflight

[简体中文](Upgrade.zh-CN.md)

Run preflight before changing an existing installation:

```sh
investorch doctor
investorch upgrade check --json
```

Both commands are offline and read-only. They do not create an ApplicationHost, connect providers/MCP, initialize storage, rewrite configuration or update Skills. Use `--root /path/to/root` to inspect a separate installation. Exit status is `0` for healthy/disabled checks, `1` for warnings or unknown results, and `2` for errors. Inspect each check's remediation; an untested connection or missing optional data is not proof of corruption.

## Recommended sequence

1. Inspect preflight results and stop all clients/writers.
2. Create and verify an [offline backup](Backup_and_Recovery.md). Keep the archive and previous package until the upgraded state has been checked.
3. Install the selected package using your existing installation method. No release or tag is created by the upgrade commands.
4. Resolve any explicit Portfolio migration requirement below.
5. Run `investorch --update` when you want the new package's managed built-in Skill contents, then restart.
6. Run Doctor again, open Web/TUI/plain, and check existing Sessions, Portfolio records and Skill enabled state.

In a source checkout, prefix commands with `uv run --locked`.

## Local configuration compatibility

Python Schema supplies defaults; `~/.investorch/investorch.toml` supplies user overrides and secrets. Existing unversioned Local TOML is recognized as legacy. Normal configuration loading saves the original bytes as `investorch.toml.pre-schema.bak`, retains explicit overrides, materializes the historical model defaults and writes `config_version = 1`. New installations have no implicit Provider and use Web Setup.

`doctor`, `upgrade check`, `backup create` and `--update` load with migration disabled. They can inspect the legacy effective configuration without rewriting it. If the pre-schema backup already exists with different content, normal startup refuses to replace it; retain both configurations and resolve the discrepancy before retrying.

Invalid TOML and invalid values must be repaired or restored from a known-good backup. Do not delete the root or its databases to make startup succeed. Missing model configuration or credentials can be completed through `investorch web`; TUI/plain points to Setup.

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

```sh
investorch --update
```

This replaces managed built-in contents with the installed package's copies, preserves enabled state and custom Skills, and reports installed/updated/unchanged counts. It does not initialize or migrate Session/Portfolio databases, or migrate Local TOML. In a new root it creates only the directories and Skill registry needed for the update. Normal startup never updates installed built-in contents automatically.

Fork built-ins under a new name for customization. Doctor reports missing/invalid Skills and content drift, including changes whose version string stayed the same. Restart after management changes: opening a new Session in the same process does not refresh the startup catalog.
