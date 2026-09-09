"""Narrow artifact identity and process-local runtime outcomes."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from investorch_qmt.rqalpha_live.contracts import RQAlphaLiveBootstrapSnapshot


@dataclass(frozen=True, slots=True)
class WorkerLaunchSpec:
    deployment_id: str
    portfolio_id: str
    broker_account_id: str
    deployment_dir: str
    expected_strategy_sha256: str
    history_through: date | None = None


@dataclass(frozen=True, slots=True)
class RuntimeArtifacts:
    source: bytes
    manifest: dict
    bootstrap: dict
    snapshot: RQAlphaLiveBootstrapSnapshot
    deployment_dir: Path


class RuntimeFailure(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message}")
