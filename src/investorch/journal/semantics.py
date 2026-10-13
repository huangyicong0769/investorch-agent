from collections.abc import Iterable
from dataclasses import dataclass


class JournalInconsistentError(RuntimeError):
    """Raised when durable Steer disposition evidence contradicts its source history."""


@dataclass(frozen=True, slots=True)
class SteerFacts:
    activated: frozenset[int]
    discarded: frozenset[int]
    pending: frozenset[int]


def interpret_steers(records: Iterable[dict[str, object]], *, through_seq: int | None = None) -> SteerFacts:
    """Resolve source input sequences using evidence inside a frozen Journal boundary."""
    if through_seq is not None and (type(through_seq) is not int or through_seq < 0):
        raise ValueError("through_seq must be a non-negative integer or None")
    steers: dict[int, str] = {}
    activated: set[int] = set()
    discarded: set[int] = set()
    for record in records:
        seq = record["seq"]
        if type(seq) is not int or seq < 1:
            raise JournalInconsistentError("Durable user Steer history has an invalid sequence")
        if through_seq is not None and seq > through_seq:
            break
        event_type = record["type"]
        if event_type == "user_steer":
            run_id = record.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise JournalInconsistentError("Durable user Steer is missing its Run identity")
            steers[seq] = run_id
        elif event_type == "user_steers_activated":
            _apply_disposition(record, steers, activated, discarded, label="activation")
        elif event_type == "user_steers_discarded":
            _apply_disposition(record, steers, discarded, activated, label="discard", require_source_run=True)
        elif event_type == "run_ended" and "discarded_user_steer_seqs" in record:
            if record.get("status") not in {"cancelled", "failed"}:
                raise JournalInconsistentError("Durable Run-end Steer discard has an invalid Run status")
            _apply_disposition(
                record,
                steers,
                discarded,
                activated,
                label="run-end discard",
                sequence_field="discarded_user_steer_seqs",
                require_source_run=True,
                allow_existing=True,
            )
    return SteerFacts(frozenset(activated), frozenset(discarded), frozenset(set(steers) - activated - discarded))


def _apply_disposition(
    record: dict[str, object],
    steers: dict[int, str],
    target: set[int],
    conflicting: set[int],
    *,
    label: str,
    sequence_field: str = "user_steer_seqs",
    require_source_run: bool = False,
    allow_existing: bool = False,
) -> None:
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise JournalInconsistentError(f"Durable user Steer {label} is missing its Run identity")
    raw_seqs = record.get(sequence_field)
    if not isinstance(raw_seqs, list) or not raw_seqs:
        raise JournalInconsistentError(f"Durable user Steer {label} has no target sequences")
    if any(type(seq) is not int or seq < 1 for seq in raw_seqs) or len(set(raw_seqs)) != len(raw_seqs):
        raise JournalInconsistentError(f"Durable user Steer {label} has invalid target sequences")
    for seq in raw_seqs:
        if (
            seq not in steers
            or (require_source_run and steers[seq] != run_id)
            or (seq in target and not allow_existing)
            or seq in conflicting
        ):
            raise JournalInconsistentError(f"Durable user Steer {label} does not match pending instruction history")
        target.add(seq)
