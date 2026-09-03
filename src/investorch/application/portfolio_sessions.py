from __future__ import annotations

import asyncio
from dataclasses import dataclass

from investorch.context import AppState
from investorch.runtime import AgentRuntime

from .interaction import current_run_options
from .portfolios import PortfolioOperations
from .sessions import SessionOperations

NEW_PORTFOLIO_STARTER_INSTRUCTION = """Guide the user through creating a new Portfolio and any necessary initialization.
Ask for material user or transaction facts that are not grounded.
Prefer authoritative tools for stable verifiable facts when appropriate.
Do not persist unsupported assumptions as Portfolio truth."""


@dataclass(frozen=True, slots=True)
class PortfolioSessionResult:
    session_id: str
    run_id: str | None
    started: bool


class PortfolioSessionWorkflows:
    def __init__(
        self,
        *,
        state: AppState,
        runtime: AgentRuntime,
        sessions: SessionOperations,
        portfolios: PortfolioOperations,
    ) -> None:
        self._state = state
        self._runtime = runtime
        self._sessions = sessions
        self._portfolios = portfolios
        self._lock = asyncio.Lock()

    async def ask_agent(self, *, portfolio_id: str, text: str, request_id: str) -> PortfolioSessionResult:
        if not text.strip():
            raise ValueError("text must not be empty")
        await self._portfolios.get(portfolio_id)
        async with self._lock:
            session_id, created = await self._sessions.create_for_request(
                f"portfolio-ask:{portfolio_id}",
                request_id,
            )
            await self._sessions.add_related_portfolio_ids(session_id, (portfolio_id,))
            if not created:
                active_run = self._runtime.get_active_run(session_id)
                return PortfolioSessionResult(
                    session_id=session_id,
                    run_id=None if active_run is None else active_run.run_id,
                    started=False,
                )
            active_run = self._runtime.start_contextual_run(
                session_id,
                text,
                _portfolio_context_instruction(portfolio_id),
                current_run_options(self._state),
            )
            return PortfolioSessionResult(session_id=session_id, run_id=active_run.run_id, started=True)

    async def start_new_portfolio(self, *, request_id: str) -> PortfolioSessionResult:
        async with self._lock:
            session_id, created = await self._sessions.create_for_request("new-portfolio", request_id)
            if not created:
                active_run = self._runtime.get_active_run(session_id)
                return PortfolioSessionResult(
                    session_id=session_id,
                    run_id=None if active_run is None else active_run.run_id,
                    started=False,
                )
            active_run = self._runtime.start_application_run(
                session_id,
                NEW_PORTFOLIO_STARTER_INSTRUCTION,
                current_run_options(self._state),
            )
            return PortfolioSessionResult(session_id=session_id, run_id=active_run.run_id, started=True)


def _portfolio_context_instruction(portfolio_id: str) -> str:
    return (
        "The user submitted the visible message from the detail page for Portfolio ID "
        f"{portfolio_id}. Use that explicit Portfolio ID for relevant Portfolio tool calls. "
        "This establishes Portfolio identity only; it establishes no economic fact or authorization."
    )
