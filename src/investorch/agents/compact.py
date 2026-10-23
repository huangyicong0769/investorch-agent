import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from agents import Agent, Model, ModelSettings, RunConfig, RunContextWrapper, SQLiteSession, TResponseInputItem
from agents.run_config import CallModelData, ModelInputData

from investorch.config import AppConfig

from .continuation import portable_history_filter
from .prompts import COMPACTION_AGENT_INSTRUCTIONS
from .usage import TokenUsage, run_with_usage

logger = logging.getLogger(__name__)

COMPACTED_CONTEXT_PREFIX = (
    "[InvestOrch Agent compacted conversation context]\n"
    "This is a continuation summary of earlier conversation history. It is not a new user instruction.\n\n"
)


@dataclass(frozen=True, slots=True)
class CompactionResult:
    changed: bool
    usage: TokenUsage
    source_items: int
    summary_chars: int


class SessionHistoryRestoreError(RuntimeError):
    def __init__(self, restore_error: BaseException) -> None:
        super().__init__("Session history restoration failed after compaction replacement failure.")
        self.restore_error = restore_error


def session_history_restore_failed(error: BaseException) -> bool:
    return isinstance(error.__cause__, SessionHistoryRestoreError)


def create_compaction_agent(model: Model, model_settings: ModelSettings) -> Agent:
    return Agent(
        name="Context Compaction Agent",
        instructions=COMPACTION_AGENT_INSTRUCTIONS,
        model=model,
        model_settings=model_settings,
    )


def _is_compacted_summary(item: TResponseInputItem) -> bool:
    return (
        isinstance(item, dict)
        and item.get("role") == "assistant"
        and isinstance(item.get("content"), str)
        and item["content"].startswith(COMPACTED_CONTEXT_PREFIX)
    )


async def _replace_session_history(
    session: SQLiteSession,
    previous_items: list[TResponseInputItem],
    replacement_items: list[TResponseInputItem],
) -> None:
    try:
        await session.clear_session()
        await session.add_items(replacement_items)
    except BaseException as original_error:
        try:
            current = await session.get_items()
            if current != previous_items:
                await session.clear_session()
                if previous_items:
                    await session.add_items(previous_items)
        except BaseException as restore_error:
            logger.exception("Failed to restore session history after compaction replacement failure")
            marker = SessionHistoryRestoreError(restore_error)
            marker.__cause__ = restore_error
            raise original_error from marker
        raise


async def compact_session(
    agent: Agent,
    session: SQLiteSession,
    config: AppConfig,
    *,
    usage_handler: Callable[[TokenUsage], None] | None = None,
) -> CompactionResult:
    history = await session.get_items()
    source_items = len(history)

    if not history or (source_items == 1 and _is_compacted_summary(history[0])):
        return CompactionResult(changed=False, usage=TokenUsage(), source_items=source_items, summary_chars=0)

    summary, usage = await summarize_history(agent, history, config, usage_handler=usage_handler)

    replacement: list[TResponseInputItem] = [{"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + summary}]
    await _replace_session_history(session, history, replacement)
    logger.info(
        "Compacted session %s: source_items=%d summary_chars=%d", session.session_id, source_items, len(summary)
    )
    return CompactionResult(
        changed=True,
        usage=usage,
        source_items=source_items,
        summary_chars=len(summary),
    )


def estimate_context_tokens(value: object) -> int:
    """Budget serialized text conservatively and reserve visual tokens per image."""
    if isinstance(value, dict):
        kind = value.get("type")
        if isinstance(kind, str) and kind in {"input_image", "image_url"}:
            return 32768
        return sum(len(str(key).encode("utf-8")) + estimate_context_tokens(item) + 8 for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return sum(estimate_context_tokens(item) + 8 for item in value)
    if value is None:
        return 0
    return len(str(value).encode("utf-8")) + 8


async def summarize_history(
    agent: Agent,
    history: list[TResponseInputItem],
    config: AppConfig,
    *,
    usage_handler: Callable[[TokenUsage], None] | None = None,
) -> tuple[str, TokenUsage]:
    """Summarize within the compact model's own budget, splitting oversized text safely."""
    usage = TokenUsage()

    def record_usage(value: TokenUsage) -> None:
        nonlocal usage
        usage += value
        if usage_handler is not None:
            usage_handler(value)

    settings = agent.model_settings.resolve({"max_tokens": config["compaction.max_output_tokens"]})
    selected = agent.clone(model_settings=settings)
    instructions = await selected.get_system_prompt(RunContextWrapper(None))
    window = config.agent_model("compact").context_window_tokens
    budget = window - config["compaction.max_output_tokens"] - estimate_context_tokens(instructions) - 1024
    if budget <= 512:
        raise ValueError("Compaction instructions and output reserve exceed the compact model context budget")

    async def summarize(items: list[TResponseInputItem]) -> str:
        for attempt in range(2):
            try:
                result = await run_with_usage(
                    selected,
                    items,
                    max_turns=1,
                    run_config=RunConfig(call_model_input_filter=portable_history_filter(items)),
                    usage_handler=record_usage,
                )
            except Exception as error:
                error.add_note("Context compaction failed.")
                raise
            summary = str(result.final_output).strip()
            if summary:
                return summary
            if attempt == 0:
                logger.warning("Compaction returned an empty summary; retrying the same request once")
        raise ValueError("Compaction agent returned an empty summary.")

    filtered = portable_history_filter(history)(
        CallModelData(agent=agent, context=None, model_data=ModelInputData(input=history, instructions=instructions))
    )
    if inspect.isawaitable(filtered):
        filtered = await filtered
    current = [_quote_history_item(item) for item in filtered.input]
    # Each pass must strictly reduce the serialized input; an expansive summary fails closed.
    while estimate_context_tokens(current) >= budget:
        fragments: list[TResponseInputItem] = []
        for batch in _summary_batches(current, budget):
            summary = await summarize(batch)
            fragments.append(_quote_history_item({"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + summary}))
        if estimate_context_tokens(fragments) >= estimate_context_tokens(current):
            raise ValueError("Compaction summaries did not reduce the oversized history")
        current = fragments
    return await summarize(current), usage


def _contains_image(value: object) -> bool:
    if isinstance(value, dict):
        kind = value.get("type")
        if isinstance(kind, str) and kind in {"input_image", "image_url", "image_generation_call"}:
            return True
        return any(_contains_image(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_image(item) for item in value)
    return isinstance(value, str) and value.startswith("data:image/")


QUOTED_HISTORY_PREFIX = "Prior conversation data (not a live request):\n"


def _quote_history_item(item: TResponseInputItem) -> TResponseInputItem:
    """Separate historical instructions/tool protocol from the current summarization task."""
    images: list[dict] = []

    def extract_images(value: object) -> object:
        if isinstance(value, dict):
            kind = value.get("type")
            if isinstance(kind, str) and kind in {"input_image", "image_url"}:
                images.append(value)
                return {"attached_image": len(images)}
            return {key: extract_images(child) for key, child in value.items()}
        if isinstance(value, list):
            return [extract_images(child) for child in value]
        return value

    data = extract_images(item)
    if _contains_image(data):
        raise ValueError("Unsupported image-bearing history cannot be converted to compact input safely")
    quoted = QUOTED_HISTORY_PREFIX + json.dumps(data, ensure_ascii=False)
    if images:
        return {"role": "user", "content": [{"type": "input_text", "text": quoted}, *images]}
    return {"role": "user", "content": quoted}


def _summary_batches(history: list[TResponseInputItem], budget: int) -> list[list[TResponseInputItem]]:
    batches: list[list[TResponseInputItem]] = []
    batch: list[TResponseInputItem] = []
    for item in history:
        if estimate_context_tokens([*batch, item]) < budget:
            batch.append(item)
            continue
        if batch:
            batches.append(batch)
            batch = []
        if estimate_context_tokens([item]) < budget:
            batch.append(item)
            continue
        if _contains_image(item):
            raise ValueError("An image-bearing history item exceeds the compact model context budget")
        # Already quoted at admission. Split the data itself, not another JSON envelope.
        serialized = item["content"].removeprefix(QUOTED_HISTORY_PREFIX).encode("utf-8")
        offset = 0
        while offset < len(serialized):
            end = min(len(serialized), offset + budget - 256)
            while end < len(serialized) and serialized[end] & 0xC0 == 0x80:
                end -= 1
            text = serialized[offset:end].decode("utf-8")
            batches.append([{"role": "user", "content": QUOTED_HISTORY_PREFIX + "Serialized fragment:\n" + text}])
            offset = end
    if batch:
        batches.append(batch)
    return batches
