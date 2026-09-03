from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents import Agent

from investorch.agents import TokenUsage, compact_review_instructions
from investorch.config import AppConfig
from investorch.journal import read_session_journal


class ReviewContextError(RuntimeError):
    """Raised when durable user authorization evidence cannot be reconstructed safely."""


@dataclass(frozen=True, slots=True)
class PreparedReviewContext:
    text: str
    instruction_count: int
    instruction_head_seq: int | None
    compacted: bool
    usage: TokenUsage


class ReviewContext:
    """Reconstruct effective user-authored instructions at a frozen journal boundary."""

    def __init__(self, *, config: AppConfig, compaction_agent: Agent | None = None) -> None:
        self._config = config
        self._journal_dir = config.session_journal_dir
        self._compaction_agent = compaction_agent

    async def prepare(self, session_id: str, instruction_head_seq: int | None) -> PreparedReviewContext:
        if instruction_head_seq is None:
            return PreparedReviewContext(
                text="",
                instruction_count=0,
                instruction_head_seq=None,
                compacted=False,
                usage=TokenUsage(),
            )
        if type(instruction_head_seq) is not int or instruction_head_seq < 1:
            raise ReviewContextError("Review instruction boundary must be a positive journal sequence")
        try:
            records = await asyncio.to_thread(
                read_session_journal,
                self._journal_dir,
                session_id,
                through_seq=instruction_head_seq,
            )
            prepared = _prepare_records(records, instruction_head_seq)
        except ReviewContextError:
            raise
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise ReviewContextError("Durable user instruction history is unavailable or invalid") from exc
        if len(prepared.text) <= self._config["permission.max_user_instruction_chars"]:
            return prepared
        if self._compaction_agent is None:
            raise ReviewContextError("Review instruction history requires compaction, but no compactor is available")
        try:
            compacted = await compact_review_instructions(self._compaction_agent, self._config, prepared.text)
        except Exception as exc:
            raise ReviewContextError("Review instruction history could not be compacted safely") from exc
        return PreparedReviewContext(
            text=compacted.text,
            instruction_count=prepared.instruction_count,
            instruction_head_seq=prepared.instruction_head_seq,
            compacted=True,
            usage=compacted.usage,
        )


def _prepare_records(records: list[dict[str, object]], instruction_head_seq: int) -> PreparedReviewContext:
    if not records or records[-1].get("seq") != instruction_head_seq:
        raise ReviewContextError("Review instruction boundary is missing from durable history")
    if records[-1].get("type") not in {"user_message", "user_steer", "user_steers_activated"}:
        raise ReviewContextError("Review instruction boundary is not an active user instruction event")

    instructions: list[tuple[int, str, str]] = []
    steers: dict[int, tuple[str, str]] = {}
    activated: set[int] = set()
    discarded: set[int] = set()
    for record in records:
        seq = record["seq"]
        assert isinstance(seq, int)
        event_type = record["type"]
        if event_type == "user_message":
            instructions.append((seq, "user_message", _instruction_text(record)))
        elif event_type == "user_steer":
            if not isinstance(record.get("run_id"), str) or not record["run_id"]:
                raise ReviewContextError("Durable user Steer is missing its Run identity")
            steers[seq] = (record["run_id"], _instruction_text(record))
        elif event_type == "user_steers_activated":
            _apply_disposition(record, steers, activated, discarded, label="activation")
        elif event_type == "user_steers_discarded":
            _apply_disposition(
                record,
                steers,
                discarded,
                activated,
                label="discard",
                require_source_run=True,
            )
        elif event_type == "run_ended" and "discarded_user_steer_seqs" in record:
            if record.get("status") not in {"cancelled", "failed"}:
                raise ReviewContextError("Durable Run-end Steer discard has an invalid Run status")
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

    unproven = set(steers) - activated - discarded
    if unproven:
        raise ReviewContextError("Durable user Steer activation or discard cannot be proven at the review boundary")
    instructions.extend((seq, "user_steer", steers[seq][1]) for seq in activated)
    instructions.sort(key=lambda item: item[0])
    text = "\n\n".join(f"[{event_type} seq={seq}]\n{value}" for seq, event_type, value in instructions)
    return PreparedReviewContext(
        text=text,
        instruction_count=len(instructions),
        instruction_head_seq=instruction_head_seq,
        compacted=False,
        usage=TokenUsage(),
    )


def _instruction_text(record: dict[str, object]) -> str:
    text = record.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ReviewContextError("Durable user instruction text is missing or empty")
    return text


def _apply_disposition(
    record: dict[str, object],
    steers: dict[int, tuple[str, str]],
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
        raise ReviewContextError(f"Durable user Steer {label} is missing its Run identity")
    raw_seqs = record.get(sequence_field)
    if not isinstance(raw_seqs, list) or not raw_seqs:
        raise ReviewContextError(f"Durable user Steer {label} has no target sequences")
    if any(type(seq) is not int or seq < 1 for seq in raw_seqs) or len(set(raw_seqs)) != len(raw_seqs):
        raise ReviewContextError(f"Durable user Steer {label} has invalid target sequences")
    for seq in raw_seqs:
        if (
            seq not in steers
            or (require_source_run and steers[seq][0] != run_id)
            or (seq in target and not allow_existing)
            or seq in conflicting
        ):
            raise ReviewContextError(f"Durable user Steer {label} does not match pending instruction history")
        target.add(seq)
