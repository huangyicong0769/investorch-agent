from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from agents import FunctionTool, ModelBehaviorError, ModelSettings, OpenAIResponsesModel, WebSearchTool, tool_namespace
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import ModelTracing
from agents.tool import Tool
from agents.usage import Usage
from openai import APIStatusError, AsyncOpenAI
from openai.types.responses import Response
from openai.types.responses.response_prompt_param import ResponsePromptParam

from investorch.llm.oauth import USAGE_URL

# Only documented categories and conventional API categories may enter user-visible text.
_INFERENCE_CODES = {
    "subscription_sharing_user_not_eligible": 403,
    "subscription_sharing_usage_limit_exceeded": 429,
    "subscription_sharing_usage_unavailable": 503,
    "subscription_sharing_unsupported_capability": 400,
    "subscription_sharing_route_not_supported": 403,
    "subscription_sharing_invalid_user": 401,
    "chatpass_v2_scope_not_authorized": 403,
    "chatpass_v2_invalid_authorization_context": 403,
    "subscription_sharing_user_unavailable": 503,
    "usage_limit_reached": 429,
    "rate_limit_exceeded": 429,
    "insufficient_quota": 429,
    "invalid_api_key": 401,
}


class ChatGPTInferenceError(Exception):
    """Credential-free provider category and recovery guidance safe for Run output."""

    def __init__(self, code: object, status: int = 502):
        self.code = code if isinstance(code, str) and code in _INFERENCE_CODES else f"chatgpt_http_{status}"
        self.status = status
        if self.code == "subscription_sharing_user_not_eligible":
            action = "This account, workspace or policy is not eligible for ChatGPT plan usage. Check the account's access policy; repeating sign-in will not resolve this restriction."
        elif status == 401:
            action = "Check the selected ChatGPT account and plan-use permission. Sign in again from Settings or /model login if access was revoked."
        elif status == 403:
            action = "ChatGPT policy or permission blocked this request. Check the selected account, granted permissions and supported region or route before retrying."
        elif status == 429:
            action = f"ChatGPT usage or rate limit reached. Pause requests and check Manage Usage: {USAGE_URL}. A plan or app limit may apply."
        elif status == 400:
            action = (
                "ChatGPT rejected this request capability. Check the selected model, input and Tools before retrying."
            )
        else:
            action = "ChatGPT is temporarily unavailable. Credentials are retained; try again later."
        super().__init__(f"ChatGPT request failed ({self.code}, HTTP {status}). {action}")


UNSUPPORTED_FIELDS = frozenset(
    {
        "background",
        "conversation",
        "max_output_tokens",
        "max_tool_calls",
        "metadata",
        "moderation",
        "multi_agent",
        "prompt",
        "prompt_cache_retention",
        "safety_identifier",
        "temperature",
        "top_logprobs",
        "top_p",
        "truncation",
        "user",
        "previous_response_id",
    }
)


def chatgpt_tools(tools: list[Tool]) -> list[Tool]:
    """Copy local functions into a namespace before SDK dispatch and approval binding."""
    functions = [tool for tool in tools if isinstance(tool, FunctionTool) and tool.qualified_name == tool.name]
    wrapped = iter(tool_namespace(name="investorch", description="InvestOrch local tools", tools=functions))
    result: list[Tool] = []
    for tool in tools:
        if isinstance(tool, FunctionTool):
            result.append(next(wrapped) if tool.qualified_name == tool.name else tool)
        elif isinstance(tool, WebSearchTool):
            result.append(tool)
        else:
            raise ModelBehaviorError("This hosted tool is unavailable through ChatGPT plan usage.")
    return result


def _settings(settings: ModelSettings) -> ModelSettings:
    def compatible(values: dict[str, Any] | None) -> dict[str, Any] | None:
        if values is None:
            return None
        return {key: value for key, value in values.items() if key not in UNSUPPORTED_FIELDS | {"store", "stream"}}

    return replace(
        settings,
        store=False,
        max_tokens=None,
        temperature=None,
        top_p=None,
        truncation=None,
        metadata=None,
        prompt_cache_retention=None,
        top_logprobs=None,
        extra_args=compatible(settings.extra_args),
        extra_body=compatible(settings.extra_body),
    )


class ChatGPTResponsesModel(OpenAIResponsesModel):
    """Use the SDK's Responses stream for both streamed and ordinary Runner calls."""

    def __init__(self, model: str, client: AsyncOpenAI):
        if str(client.base_url).rstrip("/") != "https://api.openai.com/v1":
            raise ValueError("ChatGPT OAuth requires the official https://api.openai.com/v1 endpoint.")
        super().__init__(model=model, openai_client=client)

    async def stream_response(
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
    ) -> AsyncIterator[TResponseStreamEvent]:
        if previous_response_id or conversation_id or prompt or handoffs:
            raise ModelBehaviorError(
                "ChatGPT HTTP requests require complete local history and do not support hosted continuation or handoffs."
            )
        for tool in tools:
            if isinstance(tool, FunctionTool):
                if tool.qualified_name == tool.name:
                    raise ModelBehaviorError("ChatGPT function tools must be namespaced before starting the Runner.")
                if tool.defer_loading:
                    raise ModelBehaviorError("Deferred tool search is unavailable through ChatGPT plan usage.")
            elif not isinstance(tool, WebSearchTool):
                raise ModelBehaviorError("This hosted tool is unavailable through ChatGPT plan usage.")
        request_input = (
            input
            if isinstance(input, str)
            else [
                {**item, "role": "developer"} if isinstance(item, dict) and item.get("role") == "system" else item
                for item in input
            ]
        )
        settings = _settings(model_settings)
        choice = settings.tool_choice
        if isinstance(choice, str) and choice not in {"auto", "required", "none"}:
            matches = [tool.qualified_name for tool in tools if isinstance(tool, FunctionTool) and tool.name == choice]
            if len(matches) == 1:
                settings = replace(settings, tool_choice=matches[0])
        completed = False
        stream = super().stream_response(
            system_instructions, request_input, settings, tools, output_schema, [], tracing
        )
        try:
            async for event in stream:
                if event.type in {"response.failed", "response.incomplete", "error", "response.error"}:
                    error = getattr(getattr(event, "response", None), "error", None) or event
                    code = getattr(error, "code", None)
                    status = _INFERENCE_CODES.get(code, 502) if isinstance(code, str) else 502
                    raise ChatGPTInferenceError(code, status)
                if event.type == "response.completed":
                    completed = True
                yield event
        except APIStatusError as error:
            raise ChatGPTInferenceError(error.code, error.status_code) from None
        finally:
            await stream.aclose()
        if not completed:
            raise ModelBehaviorError("ChatGPT stream ended without response.completed.")

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
    ) -> ModelResponse:
        final: Response | None = None
        async for event in self.stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        ):
            if event.type == "response.completed":
                final = event.response
        if final is None:
            raise ModelBehaviorError("ChatGPT stream ended without response.completed.")
        raw = final.usage
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
        return ModelResponse(
            output=final.output,
            usage=usage,
            response_id=final.id,
            request_id=getattr(final, "_request_id", None),
            raw_usage=raw.model_dump(mode="json", exclude_unset=True)
            if raw and model_settings.preserve_raw_usage
            else None,
        )
