"""Application-approved feedback for tool validation failures."""

from __future__ import annotations

from functools import partial

from agents import RunContextWrapper
from agents.decorators import tool as sdk_tool
from agents.exceptions import ModelBehaviorError
from pydantic import ValidationError

from investorch.context import AgentContext
from investorch.portfolio.schema import PortfolioStorageError


def format_tool_error(context: RunContextWrapper[AgentContext], error: Exception) -> str:
    """Expose corrective errors without SDK validation inputs or credentials."""
    # Application initialization imports the Agent's tools before its services.
    from investorch.application.portfolios import PortfolioOperationError
    from investorch.backtest.errors import BacktestDataError

    validation = error.__cause__ if isinstance(error, ModelBehaviorError) else error
    if isinstance(validation, ValidationError):
        fields = validation.errors(include_input=False, include_context=False, include_url=False)
        details = "; ".join(f"{'.'.join(map(str, field['loc']))}: {field['msg']}" for field in fields)
        message = f"Invalid JSON input: {details}"
    elif isinstance(error, ModelBehaviorError):
        message = str(error).split(":", 1)[0]
    elif isinstance(
        error,
        (ValueError, TypeError, FileNotFoundError, PortfolioStorageError, PortfolioOperationError, BacktestDataError),
    ) or (isinstance(error, RuntimeError) and str(error) == "exec_command is currently supported on macOS only"):
        message = str(error)
    else:
        return "Error: The tool failed unexpectedly. Check System Status before retrying."
    for secret in sorted(context.context.config.secrets.values(), key=len, reverse=True):
        if secret:
            message = message.replace(secret, "<redacted>")
    return f"Error: {message}"


tool = partial(sdk_tool, failure_error_function=format_tool_error)
