import logging
from dataclasses import dataclass

from agents import Agent, ModelSettings, OpenAIResponsesModel, Runner, SQLiteSession, TResponseInputItem

from investorch.config import AppConfig

from .prompts import COMPACTION_AGENT_INSTRUCTIONS
from .usage import TokenUsage

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


def create_compaction_agent(model: OpenAIResponsesModel, model_settings: ModelSettings) -> Agent:
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


async def compact_session(agent: Agent, session: SQLiteSession, config: AppConfig) -> CompactionResult:
    history = await session.get_items()
    source_items = len(history)

    if not history or (source_items == 1 and _is_compacted_summary(history[0])):
        return CompactionResult(changed=False, usage=TokenUsage(), source_items=source_items, summary_chars=0)

    settings = agent.model_settings.resolve({"max_tokens": config["compaction.max_output_tokens"]})
    result = await Runner.run(agent.clone(model_settings=settings), history, max_turns=1)
    summary = str(result.final_output).strip()

    if not summary:
        raise ValueError("Compaction agent returned an empty summary.")

    replacement: list[TResponseInputItem] = [{"role": "assistant", "content": COMPACTED_CONTEXT_PREFIX + summary}]
    await _replace_session_history(session, history, replacement)
    logger.info(
        "Compacted session %s: source_items=%d summary_chars=%d", session.session_id, source_items, len(summary)
    )
    return CompactionResult(
        changed=True,
        usage=TokenUsage.from_sdk(result.context_wrapper.usage),
        source_items=source_items,
        summary_chars=len(summary),
    )
