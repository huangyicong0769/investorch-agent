import json
from collections.abc import Awaitable, Sequence
from typing import TYPE_CHECKING, Protocol

from agents import FunctionTool
from agents.tool_context import ToolContext

from investorch.context import AgentContext
from investorch.tools.errors import tool

if TYPE_CHECKING:
    from investorch.application.interaction import UserInputSubmission
    from investorch.application.messaging import MessageImageReference


class MessageSubmission(Protocol):
    def __call__(
        self,
        *,
        source_session_id: str,
        source_run_id: str,
        target_session_id: str,
        text: str,
        images: "Sequence[MessageImageReference]",
    ) -> "Awaitable[UserInputSubmission]": ...


def make_messaging_tools(*, submit: MessageSubmission) -> list[FunctionTool]:
    # Application imports the base tools during startup; resolve these only once the Host is assembled.
    from investorch.application.messaging import MessageImageReference
    from investorch.presentation import serialize_user_input_submission

    @tool(needs_approval=True)
    async def send_message(
        context: ToolContext[AgentContext],
        target_session_id: str,
        text: str = "",
        images: list[MessageImageReference] | None = None,
    ) -> dict[str, object]:
        """Submit approved text/images to another existing, unarchived Session in this Root.

        Sender identity comes from this Run. Delivery requires the sender's Permission approval;
        it does not authorize the recipient's later tools. Receipts confirm Run/Steer/Queue submission,
        never execution completion or restart durability. Use read_session to check results, or reply
        to the trusted sender Session ID in received input. Avoid automatic ping-pong unless requested.
        Correct rejected arguments; never bypass Permission or edit another Session's Journal.

        Args:
            target_session_id: Complete exact ID from list_sessions; your own Session and archived targets are forbidden.
            text: Original message text, unchanged. May be empty only when images are present.
            images: Small image references, never Base64 or external URLs. Each has source="workspace" with a relative
                path, or source="session_event" with seq and zero-based image_index from your own committed history.
                Pending/discarded Steer images are unavailable. Optional detail overrides image detail; other fields
                must be null/omitted. Workspace files and persisted attachments use the ordinary image validation.
        """
        # SDK's strict JSON schema disallows extras, but its generated Pydantic model ignores them at execution.
        if set(json.loads(context.tool_arguments)) - {"target_session_id", "text", "images"}:
            raise ValueError("send_message accepts only target_session_id, text, and images")
        receipt = await submit(
            source_session_id=context.context.session_id,
            source_run_id=context.context.run_id,
            target_session_id=target_session_id,
            text=text,
            images=images or (),
        )
        return serialize_user_input_submission(receipt)

    return [send_message]
