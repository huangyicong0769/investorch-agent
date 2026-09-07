"""Orthogonal runtime safety values; dependency supervision belongs to B2+."""

from dataclasses import dataclass
from enum import StrEnum


class Lifecycle(StrEnum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class PortfolioSync(StrEnum):
    SYNCED = "SYNCED"
    COMMIT_PENDING = "COMMIT_PENDING"
    DESYNCED = "DESYNCED"


class DependencyHealth(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RuntimeState:
    lifecycle: Lifecycle = Lifecycle.STARTING
    portfolio_sync: PortfolioSync = PortfolioSync.DESYNCED
    required_dependencies: tuple[DependencyHealth, ...] = ()

    @property
    def can_submit_new_order(self) -> bool:
        return (
            self.lifecycle is Lifecycle.RUNNING
            and self.portfolio_sync is PortfolioSync.SYNCED
            and bool(self.required_dependencies)
            and all(health is DependencyHealth.AVAILABLE for health in self.required_dependencies)
        )
