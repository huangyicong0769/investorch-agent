from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from investorch.portfolio.schema import PortfolioConflictError


class LiveExecutionError(PortfolioConflictError):
    """Live execution cannot satisfy its persisted contract."""


class LiveDeploymentStatus(StrEnum):
    PREPARED = "PREPARED"
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class LiveDeployment:
    deployment_id: str
    portfolio_id: str
    broker_account_id: str
    strategy_source_path: str
    strategy_sha256: str
    strategy_parameters_json: str
    strategy_artifact_relpath: str
    rqalpha_version: str
    status: LiveDeploymentStatus
    created_at: datetime
    bootstrap_ledger_sequence: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    failure_reason: str | None = None

    @property
    def strategy_parameters(self) -> dict:
        return json.loads(self.strategy_parameters_json)
