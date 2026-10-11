# Management and storage qualification

Date: 2026-10-11. Local macOS, Python 3.12.10. All sources were disposable fixture directories; the real user root and real provider credentials were not used.

## Production-process and storage checks

Command:

```sh
uv run --locked --no-sync python scripts/qualify_management_storage.py
```

Exit code: **0**. The script launches the actual `investorch web` subprocess, calls its loopback HTTP API, and uses real SQLite files. No application host is mocked. Historical Portfolio data comes from the repository's literal v1–v5 SQL fixtures; v6 is produced through the existing migration. Legacy TOML examples are synthetic secret-only, partial and full configurations. The script performs no Agent inference or provider connection test.

| Check | Observed result |
| --- | --- |
| Legacy secret-only / partial / full TOML | All three entered Ready, preserved effective configuration and backed up original TOML bytes. |
| Invalid TOML | Web served Recovery; source hashes unchanged. |
| Portfolio v1 / v2 | Normal production startup reached v6; original Portfolio, ledger, cash and holding rows preserved. |
| Portfolio v3 / v4 / v5 | Web initially served Recovery without file changes; explicit migration produced an archive; subsequent startup reached Ready on v6 with core financial rows preserved. |
| Portfolio v6 | Ready; existing core financial rows preserved. |
| Active legacy live deployment | Web served Recovery; migration dry-run reported unsafe; source hashes unchanged. |
| Unknown schema / corrupt database | Web served Recovery; source hashes unchanged. |
| Doctor | Real CLI; all source hashes unchanged. |
| Backup → Verify → documented restore | Restored 16 files into a different empty root; Session identity, Portfolio ledger, disabled Skill state, Journal and Workspace content matched. |
| Restored Doctor | No errors; expected missing-credential/data/WAL warnings. |

The separate behavioral backup tests additionally cover custom state/workspace names, WAL committed rows, corrupt archives, credential exclusions, external writers and partial-output cleanup. Fixture upgrade evidence does not establish an upgrade of a particular user's historical installation. Real-provider Agent qualification and browser interaction evidence are recorded separately.

## Independent distributions

Built with:

```sh
uv build --no-sources
```

Exit code: **0**. Each artifact was then installed independently with `uv run --isolated --no-project --with ARTIFACT python package_smoke.py`, from a temporary directory containing only a copy of the smoke script.

| Artifact | SHA-256 | Result |
| --- | --- | --- |
| `investorch-0.1.0-py3-none-any.whl` | `a8ce4f467565ec6936227db3087d274c2d5ac2b7d0fbc72b367bee881d133d2c` | Exit 0 |
| `investorch-0.1.0.tar.gz` | `967efdd5b69713c923afa393a12f0cbd43d11d96cd3584c11a0d431aa5901b64` | Exit 0 |

Both runs reported:

```json
{"package":"passed","production_setup":"passed","same_process_host":"passed","provider_requests":0}
```

The smoke checks package metadata/resources, absence of bundled default TOML, optional CNEquity not installed, and all six built-ins. A new temporary HOME stays in Setup; `/setup` serves production HTML and the schema endpoint responds. Neutral model settings saved through HTTP allow the same Web process to open a real ApplicationHost and create a Session. Repeated Setup completion remains Ready, and the process shuts down gracefully. The neutral endpoint is loopback port 9; no inference is requested.

These artifact hashes identify an intermediate build before the final frontend rebuild. Rebuild both distributions and rerun the same smoke after final integration; the recorded intermediate result does not qualify subsequent artifacts.
