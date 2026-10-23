"""Bound model requests without mutating an SDK RunState that is still executing."""

import json
import logging
import math
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import pairwise

from agents import Agent, ModelResponse, SQLiteSession, TResponseInputItem

from investorch.config import AppConfig

from .compact import COMPACTED_CONTEXT_PREFIX, _replace_session_history, estimate_context_tokens, summarize_history
from .usage import TokenUsage

logger = logging.getLogger(__name__)


class ContextProjectionBudgetError(ValueError):
    def __init__(self, estimated_tokens: int, budget_tokens: int) -> None:
        super().__init__(f"Context still exceeds its input budget ({estimated_tokens} >= {budget_tokens})")
        self.estimated_tokens = estimated_tokens
        self.budget_tokens = budget_tokens


def _canonical_item(item: TResponseInputItem) -> str | None:
    if isinstance(item, dict):
        if item.get("type") == "reasoning":
            return None
        item = {
            key: value
            for key, value in item.items()
            if key not in {"id", "_agents_tool_description", "_agents_tool_title", "created_by"}
        }
        if item.get("type") == "shell_call_output" and isinstance(item.get("output"), list):
            item["output"] = [
                {key: value for key, value in chunk.items() if key != "created_by"}
                if isinstance(chunk, dict)
                else chunk
                for chunk in item["output"]
            ]
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def _item_fingerprints(items: Sequence[TResponseInputItem]) -> tuple[bytes, ...]:
    return tuple(sha256(value.encode()).digest() for item in items if (value := _canonical_item(item)) is not None)


def _added_text(previous: str, current: str) -> str:
    """Keep the changed span while excluding the unchanged prefix and suffix."""
    start = 0
    while start < min(len(previous), len(current)) and previous[start] == current[start]:
        start += 1
    old_end, new_end = len(previous), len(current)
    while old_end > start and new_end > start and previous[old_end - 1] == current[new_end - 1]:
        old_end -= 1
        new_end -= 1
    return current[start:new_end]


@dataclass(frozen=True)
class _MeasuredContext:
    items: tuple[bytes, ...]
    metadata: tuple[str, ...]
    tokens: int = 0


class ContextBudget:
    """Anchor estimates to one completed response and budget only its additions."""

    def __init__(self) -> None:
        self._measured: _MeasuredContext | None = None
        self._pending: _MeasuredContext | None = None

    @staticmethod
    def _metadata(
        instructions: str | None, tool_schemas: object, extra_input: Sequence[TResponseInputItem]
    ) -> tuple[str, ...]:
        return tuple(
            json.dumps(value, sort_keys=True, ensure_ascii=False)
            for value in (instructions, tool_schemas, list(extra_input))
        )

    def estimate(
        self,
        items: Sequence[TResponseInputItem],
        *,
        instructions: str | None,
        tool_schemas: object,
        extra_input: Sequence[TResponseInputItem] = (),
    ) -> int:
        measured = self._measured
        fingerprints = _item_fingerprints(items)
        if measured is None or fingerprints[: len(measured.items)] != measured.items:
            return (
                estimate_context_tokens(items)
                + estimate_context_tokens([instructions, tool_schemas, list(extra_input)])
                + 1024
            )
        # Opaque reasoning/SDK IDs do not establish new portable history items.
        portable = [item for item in items if _canonical_item(item) is not None]
        estimate = measured.tokens + estimate_context_tokens(portable[len(measured.items) :])
        metadata = self._metadata(instructions, tool_schemas, extra_input)
        for previous, current in zip(measured.metadata, metadata, strict=True):
            if added := _added_text(previous, current):
                estimate += estimate_context_tokens(added)
        # A replacement charges only its changed span. Deletions never subtract
        # an unknown token count from measured usage; the next response rebases it.
        return estimate

    def record_request(
        self,
        items: Sequence[TResponseInputItem],
        *,
        instructions: str | None,
        tool_schemas: object,
        extra_input: Sequence[TResponseInputItem] = (),
    ) -> None:
        self._pending = _MeasuredContext(
            _item_fingerprints(items),
            self._metadata(instructions, tool_schemas, extra_input),
        )

    def record_response(self, response: ModelResponse) -> None:
        pending, self._pending = self._pending, None
        self._measured = (
            replace(
                pending,
                items=(*pending.items, *_item_fingerprints(response.to_input_items())),
                tokens=response.usage.total_tokens,
            )
            if pending is not None and response.usage.total_tokens > 0
            else None
        )

    def reset(self) -> None:
        self._measured = self._pending = None


def _safe_prefix_lengths(items: list[TResponseInputItem]) -> list[int]:
    """Never separate a local tool invocation from its output or pending approval."""
    pending: set[str] = set()
    safe = [0]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            break
        kind = str(item.get("type", ""))
        call_id = item.get("call_id")
        if isinstance(call_id, str):
            if kind.endswith("_call"):
                pending.add(call_id)
            elif kind.endswith("_output"):
                if call_id not in pending:
                    # A reconstructed/incomplete prefix is not safe to rewrite.
                    break
                pending.remove(call_id)
        if not pending and kind != "reasoning":
            safe.append(index + 1)
    return safe


class ContextProjection:
    def __init__(
        self,
        compaction_agent: Agent,
        config: AppConfig,
        *,
        initial_history_count: int,
        usage_handler: Callable[[TokenUsage], None] | None = None,
        context_handler: Callable[[int, bool], None] | None = None,
        context_budget: ContextBudget | None = None,
    ) -> None:
        self._agent = compaction_agent
        self._config = config
        self._initial_history_count = initial_history_count
        self._usage_handler = usage_handler
        self._context_handler = context_handler
        self._budget = context_budget or ContextBudget()
        self._covered: list[TResponseInputItem] = []
        self._replacement: list[TResponseInputItem] = []
        self._flushed = False

    @property
    def changed(self) -> bool:
        """Whether this Run has replaced any request history with a summary."""
        return bool(self._covered)

    def _apply(self, items: list[TResponseInputItem]) -> list[TResponseInputItem]:
        if items[: len(self._covered)] != self._covered:
            raise ValueError("Model history changed before the compacted prefix; cannot safely reuse its summary")
        return [*self._replacement, *items[len(self._covered) :]]

    async def prepare(
        self,
        items: list[TResponseInputItem],
        *,
        instructions: str | None,
        tool_schemas: object,
        context_window_tokens: int,
        extra_input: Sequence[TResponseInputItem] = (),
    ) -> list[TResponseInputItem]:
        if self._flushed:
            raise ValueError("Cannot resume a context projection after its final flush")
        overhead = estimate_context_tokens([instructions, tool_schemas, list(extra_input)]) + 1024
        reserve = min(8192, max(256, context_window_tokens // 20))
        soft_budget = math.floor(context_window_tokens * self._config["compaction.trigger_ratio"]) - reserve
        hard_budget = context_window_tokens - reserve
        projected = self._apply(items)

        def prepared(current):
            self._budget.record_request(
                current, instructions=instructions, tool_schemas=tool_schemas, extra_input=extra_input
            )
            return [*current, *extra_input]

        estimate = self._budget.estimate(
            projected, instructions=instructions, tool_schemas=tool_schemas, extra_input=extra_input
        )
        if self._context_handler is not None:
            self._context_handler(estimate, False)
        enabled = self._config["compaction.auto_enabled"]
        if estimate < soft_budget or (not enabled and estimate < hard_budget):
            return prepared(projected)
        if not enabled:
            raise ContextProjectionBudgetError(estimate, hard_budget)
        boundaries = _safe_prefix_lengths(items)
        full_prefix = boundaries[-1]
        recent_start = full_prefix
        for start, end in pairwise(boundaries):
            group = items[start:end]
            if any(item.get("role") == "assistant" or str(item.get("type", "")).endswith("_call") for item in group):
                recent_start = start
        prefixes = list(dict.fromkeys([recent_start, full_prefix]))
        if self._context_handler is not None:
            self._context_handler(estimate, True)
        try:
            for prefix_length in prefixes:
                if prefix_length <= len(self._covered):
                    continue
                pinned = [
                    item
                    for item in items[self._initial_history_count : prefix_length]
                    if isinstance(item, dict) and item.get("role") in {"user", "developer", "system"}
                ]
                # Exact current-Run directives stay authoritative and outside the summary.
                retained = [*pinned, *items[prefix_length:]]
                if (
                    estimate_context_tokens(retained) + overhead + estimate_context_tokens(COMPACTED_CONTEXT_PREFIX)
                    >= hard_budget
                ):
                    continue
                source = self._apply(items[:prefix_length])
                summary, _usage = await summarize_history(
                    self._agent, source, self._config, usage_handler=self._usage_handler
                )
                # Keep completed progress after the instructions that produced it;
                # replaying those instructions last can make the model restart the task.
                replacement: list[TResponseInputItem] = [
                    *deepcopy(pinned),
                    {"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + summary},
                ]
                candidate = [*replacement, *items[prefix_length:]]
                compressed_estimate = estimate_context_tokens(candidate) + overhead
                if compressed_estimate >= hard_budget or compressed_estimate >= estimate:
                    continue
                self._covered = deepcopy(items[:prefix_length])
                self._replacement = replacement
                self._budget.reset()
                estimate = compressed_estimate
                return prepared(candidate)
            if estimate >= hard_budget:
                raise ContextProjectionBudgetError(estimate, hard_budget)
            return prepared(projected)
        except Exception:
            # A soft-limit maintenance failure must not interrupt a safe model request.
            # Cancellation derives from BaseException and intentionally propagates.
            if estimate >= hard_budget:
                raise
            return prepared(projected)
        finally:
            if self._context_handler is not None:
                self._context_handler(estimate, False)

    async def flush(self, session: SQLiteSession) -> bool:
        """Commit a matching prefix only after the SDK has stopped writing this session."""
        if not self._covered or self._flushed:
            return False
        history = await session.get_items()
        expected = [value for item in self._covered if (value := _canonical_item(item)) is not None]
        if not expected:
            self._warn_flush_mismatch(session)
            return False
        matched = 0
        end = 0
        for index, item in enumerate(history, start=1):
            canonical = _canonical_item(item)
            if canonical is None:
                continue
            if canonical != expected[matched]:
                self._warn_flush_mismatch(session)
                return False
            matched += 1
            if matched == len(expected):
                end = index
                break
        if matched != len(expected):
            self._warn_flush_mismatch(session)
            return False
        await _replace_session_history(session, history, [*self._replacement, *history[end:]])
        self._flushed = True
        return True

    def _warn_flush_mismatch(self, session: SQLiteSession) -> None:
        logger.warning(
            "Compacted request history could not be persisted because the session prefix changed: session=%s",
            session.session_id,
        )
