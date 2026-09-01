from __future__ import annotations

from dataclasses import dataclass

from agents import Usage


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

    @classmethod
    def from_sdk(cls, usage: Usage) -> TokenUsage:
        last_request = usage.request_usage_entries[-1] if usage.request_usage_entries else None
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
        )
