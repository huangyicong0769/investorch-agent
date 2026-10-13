from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from investorch.journal import SessionJournal, read_session_journal
from investorch.journal.semantics import JournalInconsistentError, interpret_steers
from tests.support.session_history import record_mixed_history


@pytest.mark.asyncio
async def test_steer_facts_keep_source_sequences_and_freeze_later_disposition_evidence(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    await record_mixed_history(journal, "session-a")
    records = read_session_journal(tmp_path, "session-a")

    earlier = interpret_steers(iter(records), through_seq=10)
    current = interpret_steers(iter(records), through_seq=16)

    assert earlier.activated == frozenset()
    assert earlier.discarded == frozenset({8})
    assert earlier.pending == frozenset({7})
    assert current.activated == frozenset({7})
    assert current.discarded == frozenset({8})
    assert current.pending == frozenset({16})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "failed"])
async def test_run_end_alone_proves_discard_of_its_source_run_steer(tmp_path: Path, status: str) -> None:
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    seq = await journal.record_user_steer("a", "source", "do not apply")
    now = datetime.now(UTC)
    await journal.record_run_ended("a", "source", status, now, now, discarded_user_steer_seqs=(seq,))

    facts = interpret_steers(read_session_journal(tmp_path, "a"), through_seq=2)

    assert facts.discarded == frozenset({1})
    assert facts.activated == facts.pending == frozenset()


@pytest.mark.parametrize(
    "evidence",
    [
        {"type": "user_steers_activated", "run_id": "new-run", "user_steer_seqs": [99]},
        {"type": "user_steers_activated", "run_id": "", "user_steer_seqs": [1]},
        {"type": "user_steers_activated", "run_id": "new-run", "user_steer_seqs": [True]},
        {"type": "user_steers_activated", "run_id": "new-run", "user_steer_seqs": [1, 1]},
        {"type": "user_steers_discarded", "run_id": "wrong-source", "user_steer_seqs": [1]},
        {"type": "run_ended", "run_id": "source", "status": "completed", "discarded_user_steer_seqs": [1]},
        {"type": "run_ended", "run_id": "wrong-source", "status": "failed", "discarded_user_steer_seqs": [1]},
    ],
)
def test_invalid_disposition_evidence_fails_closed(evidence: dict[str, object]) -> None:
    records = [{"seq": 1, "type": "user_steer", "run_id": "source"}, {"seq": 2, **evidence}]

    with pytest.raises(JournalInconsistentError):
        interpret_steers(records, through_seq=2)


@pytest.mark.parametrize("last_type", ["user_steers_activated", "user_steers_discarded"])
def test_duplicate_or_conflicting_disposition_fails_closed(last_type: str) -> None:
    records = [
        {"seq": 1, "type": "user_steer", "run_id": "source"},
        {"seq": 2, "type": "user_steers_activated", "run_id": "source", "user_steer_seqs": [1]},
        {"seq": 3, "type": last_type, "run_id": "source", "user_steer_seqs": [1]},
    ]

    with pytest.raises(JournalInconsistentError):
        interpret_steers(records, through_seq=3)
