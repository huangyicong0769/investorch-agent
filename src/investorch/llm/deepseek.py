from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from agents import ModelBehaviorError, ModelSettings, OpenAIResponsesModel
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem
from agents.models.interface import ModelTracing
from agents.tool import Tool
from agents.usage import Usage
from openai import APIStatusError, AsyncOpenAI
from openai.types.responses import Response, ResponseIncompleteEvent
from openai.types.responses.response import IncompleteDetails
from openai.types.responses.response_prompt_param import ResponsePromptParam

from investorch.config import AgentModelConfig
from investorch.config_schema import DEEPSEEK_FLASH_MODELS


def response_title(response: Response) -> str | None:
    title = getattr(response, "title", None)
    return (title.strip() or None) if response.status == "completed" and isinstance(title, str) else None


@dataclass
class DeepSeekModelResponse(ModelResponse):
    title: str | None = None


class DeepSeekResponseError(ModelBehaviorError):
    def __init__(self, message: str, usage: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.usage = usage

    @classmethod
    def from_http(cls, error: APIStatusError) -> "DeepSeekResponseError":
        body = error.body if isinstance(error.body, dict) else {}
        detail = body.get("error", body)
        message = detail.get("message", "") if isinstance(detail, dict) else ""
        if error.status_code == 400 and "content exists risk" in str(message).casefold():
            return cls("DeepSeek rejected the request during input safety review.", body.get("usage"))
        return cls(f"DeepSeek rejected the request (HTTP {error.status_code}).", body.get("usage"))


def response_refused(response: Response) -> bool:
    return getattr(response.incomplete_details, "reason", None) == "content_filter" or any(
        item.type == "message" and any(part.type == "refusal" for part in item.content)
        for item in response.output or ()
    )


def response_error(response: Response) -> DeepSeekResponseError | None:
    if response_refused(response):
        message = "DeepSeek withheld the answer after content safety review."
    elif response.status == "failed":
        message = "DeepSeek failed to finish the model response."
    elif response.status == "incomplete":
        message = "DeepSeek returned an incomplete model response."
    else:
        return None
    return DeepSeekResponseError(message, response.usage.model_dump(exclude_unset=True) if response.usage else None)


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
        extra = dict(request["extra_body"] or {})
        reasoning = request["reasoning"]
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else getattr(reasoning, "effort", None)
        if effort == "none" and any(tool["type"] in {"function", "custom"} for tool in request["tools"]):
            # DeepSeek rejects client tools with native chatbot features in non-thinking mode.
            extra.pop("title", None)
            extra.pop("safety_check", None)
            request["extra_body"] = extra or None
            return request
        if self._selected.safety_check is not None:
            extra["safety_check"] = {"level": self._selected.safety_check}
        hosted = []
        if self._selected.web_search:
            hosted.append({"type": "deepseek_web_search"})
        if self._selected.read_url:
            hosted.append({"type": "deepseek_read_url"})
        if hosted:
            # extra_body replaces the tools array. Keep the SDK's converted Function Tools and handoffs.
            extra["tools"] = [*request["tools"], *hosted]
        if hosted or extra.get("title") or self._selected.safety_check == "high":
            request.pop("top_logprobs", None)
        request["extra_body"] = extra or None
        return request

    async def _fetch_response(self, *args, **kwargs):
        # The SDK owns its streamed transport. Normalize DeepSeek terminal events before it logs or resolves them.
        try:
            stream = await super()._fetch_response(*args, **kwargs)
        except APIStatusError as error:
            raise DeepSeekResponseError.from_http(error) from None
        return self._safe_events(stream)

    async def _safe_events(self, stream):
        async with aclosing(stream):
            async for event in stream:
                if event.type in {"error", "response.error"}:
                    raise DeepSeekResponseError("DeepSeek failed to finish the model response.")
                if event.type not in {"response.completed", "response.failed", "response.incomplete"}:
                    yield event
                    continue
                response = event.response
                if response_refused(response):
                    response = response.model_copy(
                        update={
                            "status": "incomplete",
                            "incomplete_details": IncompleteDetails(reason="content_filter"),
                            "output": [item for item in response.output or () if item.type == "web_search_call"],
                            "error": None,
                        }
                    )
                    yield ResponseIncompleteEvent(
                        type="response.incomplete", response=response, sequence_number=event.sequence_number
                    )
                elif event.type != "response.completed":
                    yield event.model_copy(update={"response": response.model_copy(update={"error": None})})
                else:
                    yield event
                return
        raise DeepSeekResponseError("DeepSeek closed the stream before returning a final response.")

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> DeepSeekModelResponse:
        request = self._build_response_create_kwargs(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            previous_response_id,
            conversation_id,
            prompt=prompt,
        )
        try:
            result = await self._get_client().responses.create(**request)
        except APIStatusError as error:
            raise DeepSeekResponseError.from_http(error) from None
        if (failure := response_error(result)) is not None:
            raise failure
        raw = result.usage
        usage = (
            Usage(requests=1)
            if raw is None
            else Usage(
                requests=1,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                total_tokens=raw.total_tokens,
                input_tokens_details=raw.input_tokens_details,
                output_tokens_details=raw.output_tokens_details,
            )
        )
        return DeepSeekModelResponse(
            output=result.output,
            usage=usage,
            response_id=result.id,
            request_id=getattr(result, "_request_id", None),
            raw_usage=raw.model_dump(exclude_unset=True) if raw else None,
            title=response_title(result),
        )
