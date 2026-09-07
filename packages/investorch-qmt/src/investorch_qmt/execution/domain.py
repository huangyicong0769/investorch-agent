"""Execution-local authority and protocol errors."""

from dataclasses import dataclass
from datetime import datetime


class ExecutionError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int = 409):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        super().__init__(f"{code}: {message}")

    def to_wire(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True)
class ControlSession:
    session_id: str
    expires_at: datetime
