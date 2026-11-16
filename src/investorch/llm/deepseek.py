from typing import Any

from agents import ModelBehaviorError, ModelSettings, OpenAIResponsesModel
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import TResponseInputItem
from agents.tool import Tool
from openai import AsyncOpenAI
from openai.types.responses import Response
from openai.types.responses.response_prompt_param import ResponsePromptParam

from investorch.config import AgentModelConfig
from investorch.config_schema import DEEPSEEK_FLASH_MODELS


def response_title(response: Response) -> str | None:
    title = getattr(response, "title", None)
    return (title.strip() or None) if response.status == "completed" and isinstance(title, str) else None


class DeepSeekResponsesModel(OpenAIResponsesModel):
    def __init__(self, selected: AgentModelConfig, client: AsyncOpenAI) -> None:
        super().__init__(model=selected.name, openai_client=client)
        self._selected = selected

    def _build_response_create_kwargs(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        stream: bool = False,
        prompt: ResponsePromptParam | None = None,
    ) -> dict[str, Any]:
        if previous_response_id or conversation_id or prompt:
            raise ModelBehaviorError("DeepSeek Responses requires complete local input history.")
        request = super()._build_response_create_kwargs(
            system_instructions, input, model_settings, tools, output_schema, handoffs, stream=stream
        )
        if self.model not in DEEPSEEK_FLASH_MODELS or (output_schema is not None and not output_schema.is_plain_text()):
            return request
        hosted = []
        if self._selected.web_search:
            hosted.append({"type": "deepseek_web_search"})
        if self._selected.read_url:
            hosted.append({"type": "deepseek_read_url"})
        if hosted:
            # extra_body replaces the tools array. Keep the SDK's converted Function Tools and handoffs.
            request["extra_body"] = {**(request["extra_body"] or {}), "tools": [*request["tools"], *hosted]}
            request.pop("top_logprobs", None)
        return request
