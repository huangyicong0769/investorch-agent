from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from investorch.config import AppConfig
from investorch.live.domain import LiveDeployment, LiveDeploymentStatus, LiveExecutionError
from investorch.portfolio.live_storage import create_live_deployment, get_live_deployment, list_live_deployments
from investorch.portfolio.storage import get_portfolio
from investorch.strategy_source import copy_strategy_parameters, load_strategy_source


class LiveExecutionOperations:
    """Prepare immutable execution artifacts against the canonical Portfolio."""

    def __init__(self, *, config: AppConfig) -> None:
        self._config = config

    async def prepare_deployment(self, portfolio_id: str, broker_account_id: str) -> LiveDeployment:
        return await asyncio.to_thread(self._prepare, portfolio_id, broker_account_id)

    def _prepare(self, portfolio_id: str, broker_account_id: str) -> LiveDeployment:
        portfolio = get_portfolio(self._config.portfolio_db, portfolio_id)
        if portfolio is None or portfolio.strategy_binding is None:
            raise LiveExecutionError("Portfolio requires a StrategyBinding")
        source = load_strategy_source(self._config.workspace_dir, portfolio.strategy_binding.source_path)
        parameters = copy_strategy_parameters(portfolio.strategy_binding.parameters)
        deployment_id = uuid.uuid4().hex
        relative = Path("live") / "deployments" / deployment_id / "strategy.py"
        deployment = LiveDeployment(
            deployment_id,
            portfolio_id,
            broker_account_id,
            source.relative_path,
            source.sha256,
            json.dumps(parameters, allow_nan=False, sort_keys=True),
            relative.as_posix(),
            "6.3.0",
            LiveDeploymentStatus.PREPARED,
            datetime.now(UTC),
        )
        artifact = self._config.state_dir / relative
        artifact.parent.mkdir(parents=True, exist_ok=False)
        try:
            artifact.write_bytes(source.source)
            artifact.with_name("manifest.json").write_text(
                json.dumps(
                    {
                        "deployment_id": deployment_id,
                        "portfolio_id": portfolio_id,
                        "broker_account_id": broker_account_id,
                        "strategy_source_path": source.relative_path,
                        "strategy_sha256": source.sha256,
                        "strategy_parameters": parameters,
                        "rqalpha_version": deployment.rqalpha_version,
                        "created_at": deployment.created_at.isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            create_live_deployment(self._config.portfolio_db, deployment)
        except BaseException:
            shutil.rmtree(artifact.parent)
            raise
        return deployment

    async def get_deployment(self, deployment_id: str) -> LiveDeployment:
        deployment = await asyncio.to_thread(get_live_deployment, self._config.portfolio_db, deployment_id)
        if deployment is None:
            raise LiveExecutionError(f"Deployment not found: {deployment_id}")
        return deployment

    async def list_deployments(self, portfolio_id: str | None = None) -> list[LiveDeployment]:
        return await asyncio.to_thread(list_live_deployments, self._config.portfolio_db, portfolio_id)
