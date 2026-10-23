from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from agents import Agent

from investorch.agents import TokenUsage, compact_review_instructions
from investorch.agents.usage import UsageHandler
from investorch.config import AppConfig
from investorch.inputs import input_origin_from_record
from investorch.journal import read_session_journal
from investorch.journal.semantics import JournalInconsistentError, interpret_steers


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
    """Reconstruct effective user-role instructions at a frozen journal boundary."""

    def __init__(self, *, config: AppConfig, compaction_agent: Agent | None = None) -> None:
        self._config = config
        self._journal_dir = config.session_journal_dir
        self._compaction_agent = compaction_agent

    async def prepare(
        self, session_id: str, instruction_head_seq: int | None, *, usage_handler: UsageHandler | None = None
    ) -> PreparedReviewContext:
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
            compacted = await compact_review_instructions(
                self._compaction_agent, self._config, prepared.text, usage_handler=usage_handler
            )
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
    steers: dict[int, str] = {}
    for record in records:
        seq = record["seq"]
        assert isinstance(seq, int)
        event_type = record["type"]
        if event_type == "user_message":
            instructions.append((seq, "user_message", _instruction_text(record)))
        elif event_type == "user_steer":
            steers[seq] = _instruction_text(record)

    try:
        facts = interpret_steers(records, through_seq=instruction_head_seq)
    except JournalInconsistentError as exc:
        raise ReviewContextError(str(exc)) from exc
    if facts.pending:
        raise ReviewContextError("Durable user Steer activation or discard cannot be proven at the review boundary")
    instructions.extend((seq, "user_steer", steers[seq]) for seq in facts.activated)
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
    origin = input_origin_from_record(record)
    source = ""
    if origin.kind == "agent":
        session_id = json.dumps(origin.sender_session_id, ensure_ascii=False)
        run_id = json.dumps(origin.sender_run_id, ensure_ascii=False)
        source = f"[origin=agent: Session {session_id} (Run {run_id})]\n"
    text = record.get("text")
    if isinstance(text, str) and text.strip():
        return source + text
    images = record.get("images")
    if isinstance(text, str) and isinstance(images, list) and images:
        return source + f"[{len(images)} images attached]"
    raise ReviewContextError("Durable user instruction text is missing or empty")
