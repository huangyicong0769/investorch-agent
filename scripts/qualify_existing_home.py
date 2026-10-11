"""Prepare an old-release HOME, then verify it with an independently installed release."""

import argparse
import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from investorch.config import load_config
from investorch.initializer import initialize
from investorch.portfolio import (
    LedgerEntry,
    LedgerEntryType,
    OpeningCash,
    Portfolio,
    append_ledger_operation,
    create_portfolio,
    list_ledger_entries,
    list_portfolios,
)
from investorch.skills.registry import read_registry, write_registry
from investorch.storage import create_session, get_session, set_session_title

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("mode", choices=("prepare", "verify"))
parser.add_argument("--home", type=Path, required=True, help="Dedicated qualification HOME, never the real user HOME")
arguments = parser.parse_args()
home = arguments.home.resolve()
root = home / ".investorch"
prepare = arguments.mode == "prepare"
if prepare and root.exists():
    parser.error("Preparation requires an empty qualification HOME")
if not prepare and not (home / "baseline.json").is_file():
    parser.error("Verification requires a copy of the prepared HOME and baseline.json")
os.environ["HOME"] = str(home)
if prepare:
    root.mkdir(parents=True, exist_ok=True)
    (root / "investorch.toml").write_text(
        '# Baseline user override\n[secrets]\nDEEPSEEK_API_KEY = "legacy-upgrade-placeholder"\n'
    )
    config = load_config()
    initialize(config)
    create_session(config.sessions_db, "baseline-session")
    set_session_title(config.sessions_db, "baseline-session", "Existing 0.1 conversation")
    (config.session_journal_dir / "baseline-session.jsonl").write_text(
        '{"type":"user_message","text":"old session retained"}\n'
    )
    now = datetime(2026, 10, 11, tzinfo=UTC)
    create_portfolio(config.portfolio_db, Portfolio("baseline-portfolio", "Existing 0.1 portfolio", "CNY", now, now))
    append_ledger_operation(
        config.portfolio_db,
        [
            LedgerEntry(
                "opening",
                "opening-op",
                "baseline-portfolio",
                1,
                LedgerEntryType.OPENING_CASH,
                now,
                now,
                "qualification",
                OpeningCash("CNY", Decimal("1234.56")),
            )
        ],
    )
    registry = read_registry(config.state_dir / "skills.json")
    registry["skill-creator"] = replace(registry["skill-creator"], enabled=False)
    write_registry(config.state_dir / "skills.json", registry)
    (config.workspace_dir / "strategy.py").write_text("print('baseline retained')\n")
else:
    import shutil

    from package_smoke import request, running_web

    executable = shutil.which("investorch")
    assert executable is not None
    with running_web(executable, home) as (_, origin):
        assert request(origin, "/api/setup/state")["status"] == "ready"
        assert request(origin, "/api/portfolios")["portfolios"]
        assert request(origin, "/api/sessions")["sessions"]
    config = load_config()

snapshot = {
    "models": {
        role: {
            key: config[f"models.{role}.{key}"] for key in ("name", "base_url", "api_key_secret", "reasoning_effort")
        }
        for role in ("main", "title", "activity", "permission", "compact")
    },
    "credential_digest": hashlib.sha256(config.secret("DEEPSEEK_API_KEY").encode()).hexdigest(),
    "session": asdict(get_session(config.sessions_db, "baseline-session")),
    "portfolios": [asdict(p) for p in list_portfolios(config.portfolio_db)],
    "ledger": [asdict(e) for e in list_ledger_entries(config.portfolio_db, "baseline-portfolio")],
    "workspace_hashes": {
        str(p.relative_to(config.workspace_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in config.workspace_dir.rglob("*")
        if p.is_file()
    },
    "journal_hashes": {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in config.session_journal_dir.glob("*") if p.is_file()
    },
    "skill_registry": json.loads((config.state_dir / "skills.json").read_text()),
}
serial = json.dumps(snapshot, sort_keys=True, indent=2, default=str)
expected = home / "baseline.json"
if prepare:
    expected.write_text(serial)
else:
    assert serial == expected.read_text(), "Baseline data changed"
    assert (root / "investorch.toml.pre-schema.bak").read_text().startswith("# Baseline user override")
print(
    json.dumps(
        {
            "mode": arguments.mode,
            "passed": True,
            "sessions": 1,
            "portfolios": 1,
            "ledger_entries": 1,
            "workspace_files": len(snapshot["workspace_hashes"]),
            "provider_requests": 0,
        }
    )
)
