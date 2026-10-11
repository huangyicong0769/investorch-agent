# D/E management implementation acceptance

2026-10-11. Branch `feature/0.2.0-product-management-recovery`; comparison base `main` at `acb2ea3e31d7424acd123db3909e634a75b04057`. The user requested local commits and review before opening a PR. No PR, push, merge, tag or release was performed. Package version remains `0.1.0`; the changes are documented under unreleased `0.2.0`.

## Implementation

- **Configuration:** `config_schema.py` owns production defaults, strict validation and lifecycle metadata. `AppConfig` owns saved/current state, atomic TOML updates, reset, snapshots and legacy migration. Packaged default TOML is removed; its historical contents exist only as a golden test fixture. No `ConfigurationOperations` or caller/actor policy was introduced.
- **Product management:** Web Setup starts before the Agent Host and completes in the same process. Settings manages saved preferences and write-only credentials; Run Defaults remain temporary. Skills provides catalog/detail and contextual Create/Install/Edit/Fork sessions. MCP management shares domain validation and redaction with Agent tools. System Status reuses Doctor.
- **Recovery:** Doctor and upgrade preflight inspect without initialization. Existing incompatible or incomplete storage enters Recovery. Backup uses SQLite snapshots, an explicit manifest and verification; offline restore was rehearsed across directories. Existing Portfolio migration rules are retained.
- **Boundaries:** Web changes require loopback Host and same browser Origin; Vite preserves browser Host. Agent self-modification restrictions remain in tools. Skill writes still use the independent Review Agent and existing approval mechanism. No new Skill authorization transaction, online restore, remote authentication or direct QMT trading path was added.

## Quality commands

The locked Python environment was synchronized with `uv sync --locked --dev`. Final local checks:

| Command | Result |
| --- | --- |
| `uv run --locked ruff format --check .` | Exit 0; 215 files formatted |
| `uv run --locked ruff check .` | Exit 0 |
| `uv run --locked pytest` | Exit 0; **612 passed in 19.12s** |
| `cd frontend && npm ci` | Exit 0; 309 packages |
| `npm run typecheck` | Exit 0 |
| `npm run lint` | Exit 0 |
| `npm run build` | Exit 0; rebuilding the committed production bundle produced no diff |

Python commands used `--no-sync` after the locked sync and a temporary uv cache. Frontend used the available bundled Node 24.19.0 runtime; CI declares 24.20.0. Vite reports its existing large-chunk advisory (about 833 kB); the build succeeds. Optional macOS `fsevents` install lifecycle was skipped by npm policy. No browser errors were observed in the qualified workflows.

## Acceptance matrix

Evidence sources are the public behavior tests, [real-Agent report](Management_Agent_Qualification.md), [browser report](Management_Browser_Qualification.md), and [storage/distribution report](Management_Storage_Qualification.md). Each report distinguishes fixtures, real processes, real provider calls and installed artifacts.

| Plan gates | Evidence |
| --- | --- |
| C01–C07 | Golden defaults, neutral fresh root, actual legacy secret-only/partial/full startup, strict parameterized validation and unknown-field rejection |
| C08–C11 | Atomic updates, read-after-restart, preserved comments, invalid batches, simulated replacement failure, temporary hot values and restart-only rejection |
| C12–C16 | Secret redaction, caller-independent domain updates, Agent tool restrictions, immutable run/backtest snapshots and injected roots |
| W01–W06 | Canonical JSON Schema bounds, API errors/atomicity, credential non-disclosure, human permission edits, Origin/Host/content-type rejection and normal Web workflows |
| W07–W08, W10–W12 | Actual production Web subprocesses, fresh Setup, same-process Host, idempotent completion, legacy Ready and non-destructive Recovery |
| W09 | Browser connection failure/cancel/retry and real-provider success recorded in the linked reports |
| W13–W14 | Browser save/reset/cache refresh, unsaved navigation, mobile/keyboard checks; shared-client behavioral regression |
| S01–S04 | Catalog/status/resource inspection, disabled preview and ownership-sensitive actions; browser and behavioral checks |
| S05–S08 | Four real Agent session starts and idempotency; actual Create→edit→independent PASS→approval→install; WARN/BLOCK and unauthorized paths covered by existing behavior tests |
| S09 | Explicit built-in update preserves enabled/custom state; no implicit migration |
| M01–M04 | Shared domain CRUD/redaction, real browser create/edit/toggle/remove, optional integrations and restart messaging |
| D01–D04 | Read-only CLI and System Status, WAL/invalid/missing-state diagnostics, safe field errors, v1–v6 preflight |
| B01–B09 | Actual offline backup→verify→cross-directory restore, SQLite committed WAL data, exclusions, corruption/duplicates, I/O failure cleanup, recursive-target rejection and unreadable-directory failure |
| P01–P03 | Real v1–v6 database fixtures; preserved financial rows, explicit v3–v5 migration, active legacy deployment refusal, unknown/corrupt refusal |
| R01–R02, R04 | Final independent wheel/sdist installation and old-implementation HOME upgrade results are recorded in the storage report |
| R03 local | Production build and committed static-bundle verification |
| R03 remote / DoD remote CI | **Blocking for merge/release:** GitHub CI was not run because the user requires review before PR creation. Local equivalents are recorded above; they are not a remote CI result. |

## Standards review

Independent review found three actionable issues: unreadable directories were omitted from backups, credential replacement incorrectly reported hot application, and Vite rewrote Host incompatibly with the same-origin boundary. They were corrected in `8078308`, `8557aa1`, and `17cff50`, respectively. Public behavior checks and an actual Vite→production API request verified the corrections; unrelated code-smell refactors were not introduced.

## Spec review

Independent review found two actionable issues: missing databases in an existing instance could be recreated, and Doctor discarded useful sanitized configuration diagnostics. They were corrected in `a1afa0d` and `29299e3`. Existing-instance failure paths preserve files; a config-only fresh root can still enter Setup. Doctor now identifies the absolute config path and safe field/constraint errors. No additional scope-creep or confirmed mixed hot/restart defect was found.

Review totals: Standards 3 findings corrected; Spec 2 findings corrected. No unresolved code findings from either review.

## Operator finding

The owner's real Local TOML contains obsolete `observability.summary_enabled`. Strict validation rejects it, so that live root requires manual configuration repair before normal startup. The original file was left unchanged. Actual Agent qualification used only its model/credential projection in an isolated root; it does not claim to have upgraded the live installation. Doctor now identifies this field without exposing credentials.

The final real-Agent run completed all four contextual session starts and the real Create→independent PASS→manual approval→install chain. Three prior strict-harness attempts failed (one denied command request and two invalid origin arguments); those attempts are retained in the Agent report. The final harness makes the required JSON null explicit and allows bounded observation after a denied action without approving it. The successful final run needed no corrective intervention or out-of-scope approval.

The real-Agent test installed a harmless one-file Skill in a disposable root. Install/Edit/Fork were verified through actual clarification starts; later acquisition/replacement/fork mutations were not performed. Their domain and approval behavior remains covered by regression tests. Screenshots use dummy credentials; provider success is proved separately by the actual Agent report.
