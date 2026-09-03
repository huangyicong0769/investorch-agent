from __future__ import annotations

import asyncio
from dataclasses import dataclass

from investorch.config import AppConfig
from investorch.journal import read_session_journal


class ReviewContextError(RuntimeError):
    """Raised when durable user authorization evidence cannot be reconstructed safely."""


@dataclass(frozen=True, slots=True)
class PreparedReviewContext:
    text: str
    instruction_count: int
    instruction_head_seq: int | None


class ReviewContext:
    """Reconstruct effective user-authored instructions at a frozen journal boundary."""

    def __init__(self, *, config: AppConfig) -> None:
        self._journal_dir = config.session_journal_dir

    async def prepare(self, session_id: str, instruction_head_seq: int | None) -> PreparedReviewContext:
        if instruction_head_seq is None:
            return PreparedReviewContext(text="", instruction_count=0, instruction_head_seq=None)
        if type(instruction_head_seq) is not int or instruction_head_seq < 1:
            raise ReviewContextError("Review instruction boundary must be a positive journal sequence")
        try:
            records = await asyncio.to_thread(
                read_session_journal,
                self._journal_dir,
                session_id,
                through_seq=instruction_head_seq,
            )
            return _prepare_records(records, instruction_head_seq)
        except ReviewContextError:
            raise
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise ReviewContextError("Durable user instruction history is unavailable or invalid") from exc


def _prepare_records(records: list[dict[str, object]], instruction_head_seq: int) -> PreparedReviewContext:
    if not records or records[-1].get("seq") != instruction_head_seq:
        raise ReviewContextError("Review instruction boundary is missing from durable history")
    if records[-1].get("type") not in {"user_message", "user_steer", "user_steers_activated"}:
        raise ReviewContextError("Review instruction boundary is not an active user instruction event")

    instructions: list[tuple[int, str, str]] = []
    steers: dict[int, str] = {}
    activated: set[int] = set()
    for record in records:
        seq = record["seq"]
        assert isinstance(seq, int)
        event_type = record["type"]
        if event_type == "user_message":
            instructions.append((seq, "user_message", _instruction_text(record)))
        elif event_type == "user_steer":
            if not isinstance(record.get("run_id"), str) or not record["run_id"]:
                raise ReviewContextError("Durable user Steer is missing its Run identity")
            steers[seq] = _instruction_text(record)
        elif event_type == "user_steers_activated":
            _apply_activation(record, steers, activated)

    unproven = set(steers) - activated
    if unproven:
        raise ReviewContextError("Durable user Steer activation cannot be proven at the review boundary")
    instructions.extend((seq, "user_steer", text) for seq, text in steers.items())
    instructions.sort(key=lambda item: item[0])
    text = "\n\n".join(f"[{event_type} seq={seq}]\n{value}" for seq, event_type, value in instructions)
    return PreparedReviewContext(
        text=text,
        instruction_count=len(instructions),
        instruction_head_seq=instruction_head_seq,
    )


def _instruction_text(record: dict[str, object]) -> str:
    text = record.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ReviewContextError("Durable user instruction text is missing or empty")
    return text


def _apply_activation(record: dict[str, object], steers: dict[int, str], activated: set[int]) -> None:
    if not isinstance(record.get("run_id"), str) or not record["run_id"]:
        raise ReviewContextError("Durable user Steer activation is missing its Run identity")
    raw_seqs = record.get("user_steer_seqs")
    if not isinstance(raw_seqs, list) or not raw_seqs:
        raise ReviewContextError("Durable user Steer activation has no target sequences")
    if any(type(seq) is not int or seq < 1 for seq in raw_seqs) or len(set(raw_seqs)) != len(raw_seqs):
        raise ReviewContextError("Durable user Steer activation has invalid target sequences")
    for seq in raw_seqs:
        if seq not in steers or seq in activated:
            raise ReviewContextError("Durable user Steer activation does not match pending instruction history")
        activated.add(seq)
