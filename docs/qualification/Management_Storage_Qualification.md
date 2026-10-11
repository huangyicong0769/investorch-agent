# Management and storage qualification

Date: 2026-10-11. Local macOS, Python 3.12.10. All sources were disposable fixture directories; the real user root and real provider credentials were not used.

## Production-process and storage checks

Command:

```sh
uv run --locked --no-sync python scripts/qualify_management_storage.py
```

Exit code: **0**, rerun after the final recovery fixes. The script launches the actual `investorch web` subprocess, calls its loopback HTTP API, and uses real SQLite files. No application host is mocked. Historical Portfolio data comes from the repository's literal v1–v5 SQL fixtures paired with a real Session metadata database; v6 is produced through the existing migration. Legacy TOML examples are synthetic secret-only, partial and full configurations. The script performs no Agent inference or provider connection test.

| Check | Observed result |
| --- | --- |
| Legacy secret-only / partial / full TOML | All three entered Ready, preserved effective configuration and backed up original TOML bytes. |
| Invalid TOML | Web served Recovery; source hashes unchanged. |
| Portfolio v1 / v2 | Normal production startup reached v6; original Portfolio, ledger, cash and holding rows preserved. |
| Portfolio v3 / v4 / v5 | Web initially served Recovery without file changes; explicit migration produced an archive; subsequent startup reached Ready on v6 with core financial rows preserved. |
| Portfolio v6 | Ready; existing core financial rows preserved. |
| Active legacy live deployment | Web served Recovery; migration dry-run reported unsafe; source hashes unchanged. |
| Unknown schema / corrupt database | Web served Recovery; source hashes unchanged. |
| Missing Session / Portfolio database in an existing instance | Both served Recovery; missing files were not recreated; source hashes unchanged. |
| Doctor | Real CLI; all source hashes unchanged. |
| Backup → Verify → documented restore | Restored 16 files into a different empty root; Session identity, Portfolio ledger, disabled Skill state, Journal and Workspace content matched. |
| Restored Doctor | No errors; expected missing-credential/data/WAL warnings. |

The 16 behavioral backup tests additionally cover custom state/workspace names, WAL committed rows, corrupt archives, credential exclusions, external writers, unreadable directories and partial-output cleanup. Real-provider Agent qualification and browser interaction evidence are recorded separately.

## Independent distributions

Built with:

```sh
uv build --no-sources
```

Exit code: **0**. Build source: `0db6883`, including final frontend assets from `1ba65f8` and the final backend recovery/configuration fixes. Each artifact was then installed independently with `uv run --isolated --no-project --with ARTIFACT python package_smoke.py`, from a temporary directory outside the repository. The qualified application files include `web/static/assets/index-CKNnrqbO.js`. Documentation and acceptance-evidence commits after this build do not change the application payload being qualified.

| Artifact | SHA-256 | Result |
| --- | --- | --- |
| `investorch-0.1.0-py3-none-any.whl` | `1212ebf74db1abe810d3f91167c36b508cb3eb7acb6d42e7ee81324f7df6bffa` | Clean HOME and existing HOME: exit 0 |
| `investorch-0.1.0.tar.gz` | `d2b0d43b2dd44912460cf7d571e14d3328398d5807163017c87dd3a282b15bae` | Clean HOME and existing HOME: exit 0 |

Both runs reported:

```json
{"package":"passed","production_setup":"passed","same_process_host":"passed","provider_requests":0}
```

The smoke checks package metadata/resources, absence of bundled default TOML, optional CNEquity not installed, and all six built-ins. A new temporary HOME stays in Setup; `/setup` serves production HTML and the schema endpoint responds. Neutral model settings saved through HTTP allow the same Web process to open a real ApplicationHost and create a Session. Repeated Setup completion remains Ready, and the process shuts down gracefully. The neutral endpoint is loopback port 9; no inference is requested.

## Baseline-produced existing HOME → final distributions

Baseline: actual `main` sources at `acb2ea3e31d7424acd123db3909e634a75b04057`, extracted with `git archive`. The old release's `load_config`, `initialize`, Session/Portfolio APIs and Skill registry produced a dedicated existing HOME. Its TOML contained a placeholder credential and inherited the old five-role model defaults. No current-release initializer prepared that source HOME.

Each final artifact above received a separate copy of that unchanged HOME and its baseline snapshot. `scripts/qualify_existing_home.py verify` launched the independently installed production Web command first, then compared the migrated state. Both runs exited **0** and reported:

```json
{"mode":"verify","passed":true,"sessions":1,"portfolios":1,"ledger_entries":1,"workspace_files":11,"provider_requests":0}
```

Observed preservation: all five model roles and reasoning settings, credential digest, Session identity/title/timestamps, Portfolio and opening ledger row, all 11 Workspace file hashes including six old built-ins, the disabled `skill-creator` registry entry, and Journal bytes. The original TOML was retained as `investorch.toml.pre-schema.bak`; the production API served Ready and returned the existing Session and Portfolio. This qualifies an instance produced by the actual baseline release with the stated records; it does not assert coverage of every historical user installation.

Reproduction outline, run from the repository with a new temporary directory:

```sh
repo="$PWD"
qa_dir="$(mktemp -d)"
mkdir "$qa_dir/old-source"
git archive acb2ea3e31d7424acd123db3909e634a75b04057 | tar -x -C "$qa_dir/old-source"
PYTHONPATH="$qa_dir/old-source/src" uv run --locked --no-sync python scripts/qualify_existing_home.py prepare --home "$qa_dir/baseline-home"
cp scripts/package_smoke.py scripts/qualify_existing_home.py "$qa_dir/"
cp -R "$qa_dir/baseline-home" "$qa_dir/wheel-home"
cp -R "$qa_dir/baseline-home" "$qa_dir/sdist-home"
cd "$qa_dir"
uv run --isolated --no-project --with "$repo/dist/investorch-0.1.0-py3-none-any.whl" python package_smoke.py
uv run --isolated --no-project --with "$repo/dist/investorch-0.1.0-py3-none-any.whl" python qualify_existing_home.py verify --home "$qa_dir/wheel-home"
uv run --isolated --no-project --with "$repo/dist/investorch-0.1.0.tar.gz" python package_smoke.py
uv run --isolated --no-project --with "$repo/dist/investorch-0.1.0.tar.gz" python qualify_existing_home.py verify --home "$qa_dir/sdist-home"
```
