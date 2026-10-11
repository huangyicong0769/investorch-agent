"""Configuration semantics and defaults, independent of callers and storage."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
PermissionMode = Literal["manual", "review"]
FollowUpBehavior = Literal["steer", "queue"]
REQUIRED_MODEL_AGENTS = ("main", "title", "activity", "permission", "compact")
REASONING_EFFORTS = get_args(ReasoningEffort)
PERMISSION_MODES = get_args(PermissionMode)
FOLLOW_UP_BEHAVIORS = get_args(FollowUpBehavior)

NonEmptyString = Annotated[str, Field(min_length=1, pattern=r"\S")]
EntryName = Annotated[str, Field(min_length=1, pattern=r"^[^.]+$")]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


def validate_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be an int or float")
    return value


PositiveNumber = Annotated[float, Field(gt=0), BeforeValidator(validate_number)]
NonNegativeNumber = Annotated[float, Field(ge=0), BeforeValidator(validate_number)]


def hot(default: Any, **kwargs: Any) -> Any:
    return Field(default, json_schema_extra={"apply_mode": "hot"}, **kwargs)


def restart(default: Any, **kwargs: Any) -> Any:
    return Field(default, json_schema_extra={"apply_mode": "restart_required"}, **kwargs)


class ConfigSection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, validate_default=True)


class PathsConfig(ConfigSection):
    root: NonEmptyString = Field(
        default_factory=lambda: str(Path.home() / ".investorch"),
        json_schema_extra={"apply_mode": "immutable"},
        description="Application data root; only programmatic bootstrap may select it.",
    )
    workspace: NonEmptyString = restart("workspace")
    state: NonEmptyString = restart("state")

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        root = Path(value).expanduser()
        if not root.is_absolute():
            raise ValueError("paths.root must be absolute")
        root = root.resolve()
        if root.exists() and not root.is_dir():
            raise ValueError("paths.root is not a directory")
        return str(root)


class ModelSchema(ConfigSection):
    name: NonEmptyString | None = restart(None)
    base_url: NonEmptyString | None = restart(None)
    api_key_secret: NonEmptyString | None = restart(None)
    reasoning_effort: ReasoningEffort = restart("high")
    context_window_tokens: PositiveInt | None = restart(None)


class CompactionConfig(ConfigSection):
    auto_enabled: bool = hot(True)
    trigger_ratio: Annotated[float, Field(gt=0, lt=1), BeforeValidator(validate_number)] = hot(0.80)
    max_output_tokens: PositiveInt = hot(16384)


class ActivityConfig(ConfigSection):
    max_user_message_chars: PositiveInt = hot(2000)
    max_reasoning_chars: PositiveInt = hot(3000)
    max_argument_chars: PositiveInt = hot(2000)
    max_label_chars: PositiveInt = hot(120)


class PermissionConfig(ConfigSection):
    mode: PermissionMode = restart("review")
    max_user_instruction_chars: PositiveInt = restart(16000)
    max_compacted_instruction_chars: PositiveInt = restart(12000)
    max_tool_arguments_chars: PositiveInt = restart(32000)
    max_reason_chars: PositiveInt = restart(1000)

    @model_validator(mode="before")
    @classmethod
    def reject_renamed_key(cls, value: Any) -> Any:
        if isinstance(value, dict) and "max_user_message_chars" in value:
            raise ValueError("permission.max_user_message_chars was renamed to permission.max_user_instruction_chars")
        return value

    @model_validator(mode="after")
    def validate_budgets(self) -> Self:
        if self.max_compacted_instruction_chars > self.max_user_instruction_chars:
            raise ValueError(
                "permission.max_compacted_instruction_chars must be <= permission.max_user_instruction_chars"
            )
        return self


class InteractionConfig(ConfigSection):
    follow_up_behavior: FollowUpBehavior = hot("steer")


class TUIConfig(ConfigSection):
    sidebar_width: PositiveInt = restart(30)
    sidebar_min_width: PositiveInt = restart(20)
    composer_height: Annotated[int, Field(ge=4)] = restart(7)
    activity_panel_max_height: PositiveInt = restart(8)
    activity_detail_max_height: PositiveInt = restart(12)
    approval_arguments_max_height: PositiveInt = restart(12)
    interaction_scroll_max_height: PositiveInt = restart(9)
    queue_panel_max_height: PositiveInt = restart(7)
    queue_preview_count: PositiveInt = restart(2)
    todo_panel_max_height: PositiveInt = restart(7)
    todo_collapsible_max_height: PositiveInt = restart(6)
    todo_contents_max_height: PositiveInt = restart(5)
    message_author_margin_top: PositiveInt = restart(3)
    message_author_margin_bottom: PositiveInt = restart(2)
    run_timer_interval_seconds: PositiveNumber = restart(1.0)

    @model_validator(mode="after")
    def validate_layout(self) -> Self:
        if self.sidebar_min_width > self.sidebar_width:
            raise ValueError("tui.sidebar_min_width must be <= tui.sidebar_width")
        if self.todo_collapsible_max_height > self.todo_panel_max_height:
            raise ValueError("tui.todo_collapsible_max_height must be <= tui.todo_panel_max_height")
        if self.todo_contents_max_height > self.todo_collapsible_max_height:
            raise ValueError("tui.todo_contents_max_height must be <= tui.todo_collapsible_max_height")
        if self.message_author_margin_top <= self.message_author_margin_bottom:
            raise ValueError("tui.message_author_margin_top must be > tui.message_author_margin_bottom")
        return self


class WebConfig(ConfigSection):
    default_port: Annotated[int, Field(ge=1, le=65535)] = restart(1334)
    connection_queue_capacity: PositiveInt = restart(1000)
    history_page_size: PositiveInt = restart(200)
    websocket_reconnect_base_delay_ms: NonNegativeInt = restart(500)
    websocket_reconnect_max_delay_ms: NonNegativeInt = restart(8000)
    max_notices: PositiveInt = restart(12)
    composer_max_height_px: PositiveInt = restart(160)
    unused_session_discard_delay_ms: NonNegativeInt = restart(1000)
    run_timer_interval_ms: PositiveInt = restart(1000)

    @model_validator(mode="after")
    def validate_reconnect_delays(self) -> Self:
        if self.websocket_reconnect_max_delay_ms < self.websocket_reconnect_base_delay_ms:
            raise ValueError("web.websocket_reconnect_max_delay_ms must be >= web.websocket_reconnect_base_delay_ms")
        return self


class ObservabilityConfig(ConfigSection):
    sdk_tracing_enabled: bool = restart(False)


class LoggingConfig(ConfigSection):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = restart("INFO")
    max_bytes: PositiveInt = restart(10485760)
    backup_count: NonNegativeInt = restart(5)


class RuntimeConfig(ConfigSection):
    default_timezone: NonEmptyString = hot("Asia/Shanghai")
    max_turns: PositiveInt = hot(100)

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("runtime.default_timezone is not a valid IANA timezone") from exc
        return value


class BacktestConfig(ConfigSection):
    use_cnequity: bool = restart(False)
    rqalpha_bundle_dir: NonEmptyString = restart(".rqalpha/bundle")
    artifact_dir: NonEmptyString = hot("backtests")
    default_initial_cash: PositiveNumber = hot(1000000.0)
    inspect_max_symbols: PositiveInt = hot(50)
    stock_t1: bool = hot(True)
    dividend_reinvestment: bool = hot(False)
    dividend_tax_enabled: bool = hot(True)
    capital_gain_tax_rate: NonNegativeNumber = hot(0.0)
    validate_price: bool = hot(True)
    matching_type: Literal["current_bar"] = hot("current_bar")
    price_limit: bool = hot(True)
    volume_limit: bool = hot(True)
    inactive_limit: bool = hot(True)
    slippage_model: Literal["PriceRatioSlippage"] = hot("PriceRatioSlippage")
    slippage: NonNegativeNumber = hot(0.0)
    stock_min_commission: NonNegativeNumber = hot(5.0)
    stock_commission_multiplier: NonNegativeNumber = hot(1.0)
    tax_multiplier: NonNegativeNumber = hot(1.0)
    pit_tax: bool = hot(True)


class ExecutionConfig(ConfigSection):
    default_timeout_seconds: PositiveInt = hot(30)
    max_timeout_seconds: PositiveInt = hot(300)
    background_job_dir: NonEmptyString = hot(".investorch-processes")

    @model_validator(mode="after")
    def validate_timeouts(self) -> Self:
        if self.max_timeout_seconds < self.default_timeout_seconds:
            raise ValueError("execution.max_timeout_seconds must be >= execution.default_timeout_seconds")
        return self


class ExploreConfig(ConfigSection):
    max_full_read_bytes: PositiveInt = hot(65536)
    max_read_chars: PositiveInt = hot(65536)
    max_search_results: PositiveInt = hot(50)
    max_search_snippet_chars: PositiveInt = hot(300)


class CalculateConfig(ConfigSection):
    max_expression_chars: PositiveInt = hot(1000)
    max_nodes: PositiveInt = hot(100)
    max_integer_bits: PositiveInt = hot(4096)
    max_abs_exponent: PositiveInt = hot(10000)


class MCPConfig(ConfigSection):
    default_timeout_seconds: PositiveNumber = restart(5.0)
    drop_failed_servers: bool = restart(True)
    include_server_in_tool_names: bool = restart(True)


class CNEquityConfig(ConfigSection):
    config_path: NonEmptyString = restart("configs/cnequity.toml")
    mcp_cache_tools_list: bool = restart(True)


class ImagesConfig(ConfigSection):
    max_images_per_input: PositiveInt = restart(8)
    max_image_bytes: PositiveInt = restart(8388608)
    max_total_image_bytes: PositiveInt = restart(25165824)
    default_detail: Literal["auto", "low", "high", "original"] = restart("auto")


class AppConfigSchema(ConfigSection):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    models: dict[EntryName, ModelSchema] = Field(
        default={
            "main": ModelSchema(reasoning_effort="max", context_window_tokens=1000000),
            "title": ModelSchema(reasoning_effort="none"),
            "activity": ModelSchema(reasoning_effort="none"),
            "permission": ModelSchema(reasoning_effort="high"),
            "compact": ModelSchema(reasoning_effort="high", context_window_tokens=1000000),
        },
        json_schema_extra={"apply_mode": "restart_required"},
    )
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)
    tui: TUIConfig = Field(default_factory=TUIConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    explore: ExploreConfig = Field(default_factory=ExploreConfig)
    calculate: CalculateConfig = Field(default_factory=CalculateConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    cnequity: CNEquityConfig = Field(default_factory=CNEquityConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    secrets: dict[EntryName, str] = Field(
        default_factory=dict, json_schema_extra={"apply_mode": "hot", "sensitive": True}
    )

    @field_validator("models")
    @classmethod
    def validate_models(cls, value: dict[str, ModelSchema]) -> dict[str, ModelSchema]:
        for role in REQUIRED_MODEL_AGENTS:
            if role not in value:
                raise ValueError(f"Missing required model: {role}")
        if value["main"].context_window_tokens is None:
            raise ValueError("models.main.context_window_tokens must be an integer >= 1")
        return value

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        root = Path(self.paths.root)
        workspace = resolve_under_root(root, self.paths.workspace, "paths.workspace")
        state = resolve_under_root(root, self.paths.state, "paths.state")
        if workspace == root:
            raise ValueError("paths.workspace must be a strict subdirectory of paths.root")
        if state == root:
            raise ValueError("paths.state must be a strict subdirectory of paths.root")
        if workspace == state or workspace.is_relative_to(state) or state.is_relative_to(workspace):
            raise ValueError("paths.workspace and paths.state must not overlap")
        resolve_under_root(workspace, self.execution.background_job_dir, "execution.background_job_dir")
        resolve_under_root(workspace, self.backtest.artifact_dir, "backtest.artifact_dir")
        resolve_under_root(root, self.cnequity.config_path, "cnequity.config_path")
        resolve_under_root(root, self.backtest.rqalpha_bundle_dir, "backtest.rqalpha_bundle_dir")
        return self


def resolve_under_root(root: Path, value: str, key: str) -> Path:
    relative = Path(value).expanduser()
    if relative.is_absolute():
        raise ValueError(f"{key} must be relative to paths.root")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{key} escapes paths.root")
    return resolved


def config_field_metadata(key: str) -> dict[str, Any]:
    """Describe a dotted leaf using the same schema that validates its value."""
    parts = key.split(".")
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError(f"Invalid config key: {key}")
    schema = AppConfigSchema.model_json_schema()
    node = schema
    inherited: dict[str, Any] = {}
    for part in parts:
        if "$ref" in node:
            node = {**schema["$defs"][node["$ref"].split("/")[-1]], **node}
        for attribute in ("apply_mode", "sensitive"):
            if attribute in node:
                inherited[attribute] = node[attribute]
        properties = node.get("properties", {})
        if part in properties:
            node = properties[part]
        elif matching := [
            child for pattern, child in node.get("patternProperties", {}).items() if re.search(pattern, part)
        ]:
            node = matching[0]
        elif isinstance(node.get("additionalProperties"), dict):
            node = node["additionalProperties"]
        else:
            raise ValueError(f"Unknown config key: {key}")
    if "$ref" in node:
        node = {**schema["$defs"][node["$ref"].split("/")[-1]], **node}
    if node.get("type") == "object":
        raise ValueError(f"Config key must identify a field: {key}")
    return {**inherited, **node}
