"""Bounded, credential-free explanations of failed Runs for all clients."""

from __future__ import annotations

import re
from collections.abc import Iterable

from openai import APIConnectionError, APIStatusError, APITimeoutError

from investorch.agents import ContextBudgetError
from investorch.llm.models import ChatGPTInferenceError
from investorch.llm.oauth import ChatGPTAuthError


def format_run_error(error: Exception, *, secrets: Iterable[str] = ()) -> str:
    """Select public exception information; never serialize requests or tracebacks."""
    name = type(error).__name__
    if isinstance(error, (ContextBudgetError, ChatGPTInferenceError, ChatGPTAuthError)):
        message = str(error)
    elif isinstance(error, APITimeoutError):
        message = "APITimeoutError: The model request timed out."
    elif isinstance(error, APIConnectionError):
        cause = error.__cause__
        disconnected = False
        seen: set[int] = set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if (
                type(cause).__name__ == "RemoteProtocolError"
                and str(cause) == "Server disconnected without sending a response."
            ):
                disconnected = True
                break
            cause = cause.__cause__
        detail = (
            "Server disconnected without sending a response."
            if disconnected
            else "Could not connect to the model service."
        )
        message = f"{name}: {detail}"
    elif isinstance(error, APIStatusError):
        body = error.body
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            body = body["error"]
        detail = body.get("message") if isinstance(body, dict) else None
        message = f"{name} (HTTP {error.status_code}): {detail.strip() if isinstance(detail, str) and detail.strip() else 'The model service rejected the request.'}"
    else:
        # Arbitrary exception messages may contain prompts, request bodies or secrets.
        message = f"{name}: See the application log for the full error."
    if "Context compaction failed." in getattr(error, "__notes__", ()):
        message = f"Context compaction failed. {message}"
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            message = message.replace(secret, "<redacted>")
    message = re.sub(
        r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api[_-]?key|access_token|refresh_token|id_token|client_secret|password)[\"']?\s*[:=]\s*[^\r\n]*",
        r"\1: <redacted>",
        message,
    )
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", message)
    message = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "<redacted>", message)
    message = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "<redacted>", message)
    message = "".join(char for char in message if char in "\n\t" or ord(char) >= 32)
    return message[:2000] + ("…" if len(message) > 2000 else "")
