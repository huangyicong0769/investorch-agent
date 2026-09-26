# Portfolio schema v6 qualification — 2026-09-26

The implementation and local real-database migration passed the checks below. The real database found on this
machine was **v1**, not v5. The v5 path was exercised using authentic historical schema fixtures; this does **not**
fulfil the plan's separate criterion of migrating a real user v5 database. No such database was found in the local
configured state or the inspected Windows/WSL default and known project locations.

## Git and implementation

- Review base: `763b32e950a309bfefd846137802803b8ad3ad95`.
- Work branch: `codex/portfolio-v6-transition`, created from `feature/portfolio-schema-v6-migration` at the same base.
- No old execution branch merge, cherry-pick history, push, or merge back was performed.
- Canonical schema: v6; historical v1-v5 fixture sources are recorded in each SQL file.
- Fresh v6, automatic backed-up v1/v2 upgrades, explicit v3-v5 retirement and typed rejection of active deployments
  are covered. Ledger, attribution and aggregate preservation, archive hashing, failure rollback, intervening writes
  and committed WAL contents are covered.

## Real database migration

Database: `~/.investorch/state/portfolio.db`.

The preflight found no InvestOrch process or open Portfolio/session database handles. The source used DELETE journal
mode and contained no legacy live deployments. The full state directory was copied before running the migration;
a rehearsal used a separate SQLite backup copy, then the command ran against the configured real database.

```sh
uv run --locked investorch migrate portfolio --dry-run
uv run --locked investorch migrate portfolio
```

| Check | Before | After |
| --- | --- | --- |
| Schema | 1 | 6 |
| Portfolio rows | 2 | 2 |
| Ledger rows | 12 | 12 |
| Broker / BrokerAccount rows | Not present | 0 / 0 |
| Portfolio metadata / historical Ledger | Baseline captured | Exact preservation |
| Aggregate holdings / cash | Baseline captured | Exact preservation |
| Account projections | Not present | Exact copies at NULL location |
| Foreign keys | Passed | Passed |
| quick_check / integrity_check | Passed | Passed |

Full state backup:
`~/.investorch/migration-safety-backups/20260926T020958.150297Z/state/`.

SQLite backup and hashed archive:
`~/.investorch/state/migration-archives/portfolio-v1-to-v6/20260926T021101.386948Z/`.

Private qualification evidence is alongside the full state backup: preflight, dry-run and migration JSON,
application/API checks, v1-copy/v5-fixture rehearsals, and three UI screenshots. Real account data and screenshots
are not included in this repository.

## Actual App run and Portfolio inspection

Started the actual Web App with the existing configuration and migrated real state:

```sh
uv run --locked investorch web --port 18797
```

Visited the running application's Portfolio list and both detail pages in a browser. Checked names, status, cash,
all holdings, strategy bindings and recent Ledger. Expanded a trade and an opening-cash entry and checked their
identity, sequence, source, external reference, timestamps and payload fields. The empty-holdings view and the
10-holding view both rendered correctly. Screenshots were saved to the private qualification directory.

The real HTTP health, Portfolio list, both detail and both Ledger endpoints returned 200. Cash, holdings and
Ledger identities were compared with the pre-migration backup. Database economics remained unchanged after these
read-only checks. The test Web process was stopped afterwards; no Agent conversation or trading action was sent.

A migrated v5 fixture was also read through the actual application layer: an implicit multi-account mutation was
rejected; correction retained the original account; the historical live-execution entry stayed unchanged.

## Quality and review

- `uv run --locked ruff check .`: passed.
- `uv run --locked ruff format --check .`: passed, 149 files.
- `uv run --locked pytest`: **352 passed** (includes Portfolio, initialization and Web regressions).
- `uv build --no-sources`: wheel and source distribution built.
- Both distributions passed `scripts/package_smoke.py` in isolated installations without optional CNEquity/QMT.
- Targeted `uvx ty check` passed for domain, storage, schema, migration, migration CLI and CLI modules.
  Checking the full touched surface also reports four existing diagnostics in application/ledger code, independently
  reproduced at the review base: three `list` annotation shadowing errors and one optional average-cost narrowing
  error. No new type diagnostic remains.
- Standards review found command-time drift and a schema literal-normalization defect; both were fixed and tested.
- Spec review found WAL dry-run sidecar creation; it now fails before opening SQLite and leaves files unchanged.
- Public Portfolio guides now use domain/module names instead of delivery phase codes, following user review.

## Remaining limits

WAL dry-run is intentionally rejected: SQLite mode=ro can still create or modify auxiliary files. After stopping
writers, use the old version to checkpoint and switch to DELETE journal mode before dry-run. Actual migration backs
up committed WAL contents correctly. No immutable read is used to bypass WAL.

A real v5 user database qualification remains unverified because none was found. Historical v5 fixture qualification
and the real v1 migration are separate evidence. The real database is now v6; the unmerged base branch still only
supports v1 and must not be used to reopen this database. No downgrade was performed.
