from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qmt_agent.application.presentation_state import SessionPresentationStore
from qmt_agent.application.sessions import SessionOperations
from tests.support.runtime import RuntimeHarness, make_runtime_harness


@dataclass(slots=True)
class SessionHarness:
    runtime: RuntimeHarness
    operations: SessionOperations


def make_session_harness(tmp_path: Path) -> SessionHarness:
    runtime = make_runtime_harness(tmp_path)
    operations = SessionOperations(
        config=runtime.config,
        runtime=runtime.runtime,
        journal=runtime.journal,
        presentation_state=SessionPresentationStore(),
    )
    return SessionHarness(runtime=runtime, operations=operations)
