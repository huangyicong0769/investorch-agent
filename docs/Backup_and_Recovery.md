# Backup and recovery

[简体中文](Backup_and_Recovery.zh-CN.md)

InvestOrch creates local ZIP backups and verifies their integrity. Restore is a manual operation into an empty directory.

## Create an offline backup

Stop Web, TUI, plain-console clients and other processes writing the root or Workspace. Keep them stopped until the command finishes. The command checks writable open files when `lsof` is available and reports limited visibility; this is a practical check, not a freeze of other processes.

```sh
investorch backup create --output /path/outside-root/investorch-backup.zip
investorch backup verify /path/outside-root/investorch-backup.zip
```

The output directory must exist. The destination must be outside the configured root and must not already exist. Creation publishes the final archive only after verification. On POSIX its permissions are `0600`.

To use a separate root or omit backtest artifacts:

```sh
investorch backup create --root /path/to/root --output /path/to/backup.zip --exclude-artifacts
```

In a source checkout, prefix commands with `uv run --locked`.

## Contents

The archive contains Session and Portfolio SQLite snapshots, Session journals, the Skill registry, installed Skills, `MEMORY.md`, and other Workspace files. SQLite snapshots include committed WAL rows without checkpointing the source. The manifest records each file's relative path, byte count and SHA-256, database schema versions, exclusions and the configured directory layout.

By default, `investorch.toml` contains only non-secret overrides; the entire `secrets` table and model endpoints containing URL user information, query parameters or fragments are omitted. Raw `mcp.toml`, logs, background-job files, the configured RQAlpha bundle and CNEquity configuration are excluded. `--exclude-artifacts` additionally omits the configured artifact directory. Symlinks are skipped and reported. Files outside the root are not followed.

Workspace files and journals can themselves contain private information or credentials. Every backup is **unencrypted**, including the default archive. To explicitly include original Local TOML and MCP configuration:

```sh
investorch backup create --output /path/to/private-backup.zip --include-sensitive-config
```

The manifest marks this choice. Keep the archive private; the command does not upload it.

## Restore into an empty root

1. Stop all writers and run `backup verify`. Verification checks archive members, relative paths, sizes, hashes and recorded SQLite versions; it does not test provider connectivity or market data.
2. Choose a new, nonexistent target directory. Preserve the original root until the restored data has been checked.
3. From a source checkout, run the following recipe with your archive and target paths. It uses the recorded paths, including custom `paths.state` and `paths.workspace`; it does not assume directories named `state` and `workspace`.

```sh
uv run --locked python - /path/to/backup.zip /path/to/new-root <<'PY'
import os
import shutil
import sys
import zipfile
from pathlib import Path
from investorch.backup import verify_backup

archive_path = Path(sys.argv[1])
target = Path(sys.argv[2])
manifest = verify_backup(archive_path)
target.mkdir(mode=0o700, parents=False, exist_ok=False)
with zipfile.ZipFile(archive_path) as archive:
    for record in manifest["files"]:
        destination = target / record["path"]
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with archive.open(record["path"]) as source, destination.open("xb") as output:
            shutil.copyfileobj(source, output)
        if os.name == "posix":
            destination.chmod(0o600)
print("Restored layout:", manifest["layout"])
PY
investorch doctor --root /path/to/new-root --json
investorch upgrade check --root /path/to/new-root --json
```

4. Compare Session IDs/titles and journal entries, Portfolio IDs and ledger sequences, Skill enabled state, and important Workspace file hashes with the original. Reapply executable permissions to scripts that need direct execution; restored files are private by default.
5. Re-enter excluded model credentials/endpoints and MCP settings. Reinstall or download excluded market-data dependencies when needed. A warning for missing credentials or untested data is expected before this step.
6. If Portfolio migration is required, follow [Upgrade](Upgrade.md). Do not initialize a replacement database over unreadable existing data.
7. To use the restored root with normal `investorch web`/TUI/plain commands, stop clients, retain the previous `~/.investorch` under a separate backup name and move the restored directory to `~/.investorch`. Start Web Setup if credentials are missing, then compare the same records in the application.

If extraction is interrupted, keep the archive and retry into another empty directory. The product does not provide an automatic restore or rollback command.

## Recovery evidence

`tests/behavior/test_backup_behavior.py` performs a real SQLite/ZIP restore between distinct temporary roots with custom directory names. It compares Session identity/title, Portfolio ledger and cash projection, disabled Skill registration, journals, `MEMORY.md` and a strategy file. Separate cases verify committed WAL rows, source preservation, credential exclusions, writer detection and damaged-archive rejection. `tests/behavior/test_management_cli_behavior.py` also executes create/verify as subprocess commands under an isolated HOME.
