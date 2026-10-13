from datetime import UTC, datetime

from investorch.images import ImageContent, UserInput
from investorch.journal import SessionJournal
from investorch.output import AgentChanged, AssistantMessage, Reasoning, ToolCalled, ToolOutput


async def record_mixed_history(journal: SessionJournal, session_id: str) -> int:
    """Record mixed history; visible seqs are 1, 2, 4, 5, 7, 14, 15; Review head is 14."""
    image = ImageContent("data:image/png;base64,aGVsbG8=", filename="input.png", media_type="image/png")
    await journal.record_user_message(session_id, UserInput("initial instruction", (image,)))
    await journal.record_output(session_id, AssistantMessage("first answer"))
    await journal.record_output(session_id, Reasoning("private reasoning"))
    await journal.record_output(session_id, ToolCalled("calculate", '{"expression":"1+2"}'))
    await journal.record_output(session_id, ToolOutput("3"))
    await journal.record_approval(session_id, "run-a", "approval-a", "edit", None, False)
    effective = await journal.record_user_steer(session_id, "run-a", "effective guidance")
    discarded = await journal.record_user_steer(session_id, "run-a", "discarded guidance")
    await journal.record_user_steers_discarded(session_id, "run-a", (discarded,))
    now = datetime.now(UTC)
    await journal.record_run_ended(session_id, "run-a", "failed", now, now, discarded_user_steer_seqs=(discarded,))
    await journal.record_user_steers_activated(session_id, "run-b", (effective,))
    await journal.record_output(session_id, AgentChanged("Main"))
    await journal.record_activity_label(session_id, 4, "Compute sum")
    review_head = await journal.record_user_message(session_id, "next task")
    remote = ImageContent("https://example.test/image.png?credential=private", filename="result.png")
    await journal.record_output(session_id, AssistantMessage("last answer", (remote,)))
    await journal.record_user_steer(session_id, "run-b", "pending guidance")
    return review_head
