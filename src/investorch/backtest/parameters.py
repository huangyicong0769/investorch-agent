"""Typed per-run overrides; application configuration supplies defaults only."""

from pydantic import ConfigDict, Field

from investorch.config import AppConfig
from investorch.config_schema import (
    BacktestMatchingType,
    BacktestRuntimeConfig,
    BacktestSlippageModel,
    ConfigSection,
    NonNegativeNumber,
    SlippageRatio,
)


class BacktestOverrides(ConfigSection):
    """Omitted or null fields inherit their configured defaults."""

    model_config = ConfigDict(frozen=True)

    stock_t1: bool | None = Field(
        None, description="True enforces T+1 selling for stocks; false permits selling today's purchases."
    )
    dividend_reinvestment: bool | None = Field(
        None, description="True reinvests cash dividends in the same stock; false retains cash."
    )
    dividend_tax_enabled: bool | None = Field(
        None, description="True applies the engine's holding-period dividend tax on stock sales."
    )
    capital_gain_tax_rate: NonNegativeNumber | None = Field(
        None,
        description="Finite decimal rate >= 0 (0.01 = 1%) on positive net realized gains at month-end, after the engine's within-year loss offsets; 0 disables this tax.",
    )
    validate_price: bool | None = Field(
        None, description="True rejects limit-order submissions outside limit-up/down prices; separate from matching."
    )
    matching_type: BacktestMatchingType | None = Field(
        None, description="Only current_bar: match at the current daily bar's close before slippage."
    )
    price_limit: bool | None = Field(
        None, description="True restricts matching at limit-up/down; false disables that matching check."
    )
    volume_limit: bool | None = Field(
        None,
        description="True caps cumulative fills per instrument/bar at the engine's volume fraction; may partially fill.",
    )
    inactive_limit: bool | None = Field(None, description="True cancels orders on zero-volume bars.")
    slippage_model: BacktestSlippageModel | None = Field(
        None,
        description="Only PriceRatioSlippage: buy at price*(1+r), sell at price*(1-r), clipped to daily price limits.",
    )
    slippage: SlippageRatio | None = Field(
        None, description="Finite price ratio 0 <= r < 1 (0.001 = 0.1%); 0 disables slippage."
    )
    stock_min_commission: NonNegativeNumber | None = Field(
        None, description="Finite minimum stock commission per order >= 0, in account currency (CNY)."
    )
    stock_commission_multiplier: NonNegativeNumber | None = Field(
        None,
        description="Finite multiplier >= 0 of base stock commission rate 0.0008 (0.25 gives 0.0002); minimum commission is separate, so set both fields to 0 for no commission.",
    )
    tax_multiplier: NonNegativeNumber | None = Field(
        None, description="Finite multiplier >= 0 of the stock sale stamp-tax rate; 0 disables this tax."
    )
    pit_tax: bool | None = Field(
        None,
        description="True uses historical stock stamp-tax rates by trading date; false uses fixed 0.0005, not zero.",
    )


def resolve_backtest_parameters(config: AppConfig, overrides: BacktestOverrides | None = None) -> BacktestRuntimeConfig:
    values = {name: config[f"backtest.{name}"] for name in BacktestRuntimeConfig.model_fields}
    if overrides is not None:
        values.update(overrides.model_dump(exclude_none=True))
    return BacktestRuntimeConfig.model_validate(values)
