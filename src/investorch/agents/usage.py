from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any

from agents import Agent, ModelResponse, RunContextWrapper, RunHooks, Runner, RunResult, Usage

from investorch.llm.deepseek import DeepSeekResponseError


@dataclass(frozen=True, slots=True)
class TokenUsage:
    requests: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    last_request_total_tokens: int | None = None
    safety_output_tokens: int = 0
    tool_search: int = 0
    tool_fetch: int = 0
    tool_image_search: int = 0

    @classmethod
    def from_sdk(cls, usage: Usage) -> TokenUsage:
        # SDK retry entries may contain no reported tokens; they are not a zero-sized context.
        last_request = next((entry for entry in reversed(usage.request_usage_entries) if entry.total_tokens > 0), None)
        return cls(
            requests=usage.requests,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.input_tokens_details.cached_tokens,
            cache_write_input_tokens=usage.input_tokens_details.cache_write_tokens,
            output_tokens=usage.output_tokens,
            reasoning_output_tokens=usage.output_tokens_details.reasoning_tokens,
            total_tokens=usage.total_tokens,
            last_request_total_tokens=(last_request.total_tokens if last_request is not None else None),
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            requests=self.requests + other.requests,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens + other.cache_write_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens + other.reasoning_output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            last_request_total_tokens=(
                other.last_request_total_tokens
                if other.last_request_total_tokens is not None
                else self.last_request_total_tokens
            ),
            safety_output_tokens=self.safety_output_tokens + other.safety_output_tokens,
            tool_search=self.tool_search + other.tool_search,
            tool_fetch=self.tool_fetch + other.tool_fetch,
            tool_image_search=self.tool_image_search + other.tool_image_search,
        )

    @classmethod
    def from_deepseek(cls, raw: dict[str, Any], *, include_tokens: bool = False) -> TokenUsage:
        """Read billing extensions, and base usage when a failed response bypasses SDK accounting."""

        def count(obj: object, key: str) -> int:
            value = obj.get(key) if isinstance(obj, dict) else None
            return value if type(value) is int and value >= 0 else 0

        tools = raw.get("server_tool_use")
        output = raw.get("output_tokens_details")
        input_details = raw.get("input_tokens_details")
        return cls(
            requests=int(include_tokens),
            input_tokens=count(raw, "input_tokens") if include_tokens else 0,
            cached_input_tokens=count(input_details, "cached_tokens") if include_tokens else 0,
            cache_write_input_tokens=count(input_details, "cache_write_tokens") if include_tokens else 0,
            output_tokens=count(raw, "output_tokens") if include_tokens else 0,
            reasoning_output_tokens=count(output, "reasoning_tokens") if include_tokens else 0,
            total_tokens=count(raw, "total_tokens") if include_tokens else 0,
            safety_output_tokens=count(output, "safety_tokens"),
            tool_search=count(tools, "tool_search"),
            tool_fetch=count(tools, "tool_fetch"),
            tool_image_search=count(tools, "tool_image_search"),
        )


UsageHandler = Callable[[TokenUsage], None]
SessionUsageHandler = Callable[[str, TokenUsage], None]
_USAGE_COUNTERS = tuple(field.name for field in fields(TokenUsage) if field.name != "last_request_total_tokens")


class UsageTracker(RunHooks):
    """Publish each completed request once, reconciling cumulative SDK usage on exit."""

    def __init__(self, handler: UsageHandler | None = None) -> None:
        self._handler = handler
        self.usage = TokenUsage()
        self._request_entries = 0

    def record(self, usage: Usage) -> None:
        current = TokenUsage.from_sdk(usage)
        counters = {name: max(0, getattr(current, name) - getattr(self.usage, name)) for name in _USAGE_COUNTERS}
        new_entries = usage.request_usage_entries[self._request_entries :]
        self._request_entries = max(self._request_entries, len(usage.request_usage_entries))
        if not any(counters.values()):
            return
        last_request = next((entry for entry in reversed(new_entries) if entry.total_tokens > 0), None)
        delta = TokenUsage(
            **counters, last_request_total_tokens=last_request.total_tokens if last_request is not None else None
        )
        self.usage += delta
        if self._handler is not None:
            self._handler(delta)

    async def on_llm_end(self, context: RunContextWrapper, agent: Agent, response: ModelResponse) -> None:
        self.record(context.usage)
        if response.raw_usage and (delta := TokenUsage.from_deepseek(response.raw_usage)) != TokenUsage():
            self.usage += delta
            if self._handler is not None:
                self._handler(delta)


async def run_with_usage(
    agent: Agent, input: Any, *, usage_handler: UsageHandler | None = None, **kwargs: Any
) -> RunResult:
    """Retain usage even when response validation fails before the SDK's end hook."""
    context = kwargs.pop("context", None)
    if not isinstance(context, RunContextWrapper):
        context = RunContextWrapper(context=context)
    tracker = UsageTracker(usage_handler)
    try:
        return await Runner.run(agent, input, context=context, hooks=tracker, **kwargs)
    except DeepSeekResponseError as error:
        if isinstance(error.usage, dict) and usage_handler is not None:
            usage_handler(TokenUsage.from_deepseek(error.usage, include_tokens=True))
        raise
    finally:
        tracker.record(context.usage)
