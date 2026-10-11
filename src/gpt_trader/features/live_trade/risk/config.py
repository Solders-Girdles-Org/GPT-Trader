"""
Risk configuration for live trading.

Supports both spot and CFM futures risk parameters.

This is the canonical location for RiskConfig.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from gpt_trader.config.config_utilities import kill_switch_env_engaged

RISK_CONFIG_ENV_KEYS = [
    "RISK_MAX_LEVERAGE",
    "RISK_MAX_EXPOSURE_PCT",
    "RISK_MAX_POSITION_PCT_PER_SYMBOL",
    "RISK_UNFILLED_ORDER_ALERT_SECONDS",
    # CFM-specific keys
    "CFM_MAX_LEVERAGE",
    "CFM_MIN_LIQUIDATION_BUFFER_PCT",
    "CFM_MAX_EXPOSURE_PCT",
    "CFM_MAX_POSITION_SIZE_PCT",
]

RISK_CONFIG_ENV_ALIASES: dict[str, str] = {}


@dataclass
class RiskConfig:
    """Risk configuration for live trading.

    This dataclass holds all risk-related parameters including:
    - General limits (leverage, loss limits, exposure caps)
    - Per-symbol limits (position sizes, leverage caps)
    - CFM futures-specific parameters
    - API health and degradation thresholds
    - WebSocket monitoring settings

    Example::

        from gpt_trader.features.live_trade.risk import RiskConfig

        config = RiskConfig(
            max_leverage=3,
            daily_loss_limit_pct=0.05,
            max_position_pct_per_symbol=0.2,
        )
    """

    # General risk parameters
    max_leverage: int = 5
    daily_loss_limit_pct: float = 0.05  # Percentage of equity (0.05 = 5%)
    max_exposure_pct: float = 0.8
    max_position_pct_per_symbol: float = 0.2

    # Additional risk parameters
    min_liquidation_buffer_pct: float = 0.1
    leverage_max_per_symbol: dict[str, int] = field(default_factory=dict)
    max_notional_per_symbol: dict[str, Decimal] = field(default_factory=dict)
    slippage_guard_bps: int = 50
    kill_switch_enabled: bool = False
    reduce_only_mode: bool = False

    # Day/night time window configuration
    daytime_start_utc: str = "09:00"
    daytime_end_utc: str = "17:00"
    day_leverage_max_per_symbol: dict[str, int] = field(default_factory=dict)
    night_leverage_max_per_symbol: dict[str, int] = field(default_factory=dict)

    # MMR (Maintenance Margin Requirement) projection configuration
    day_mmr_per_symbol: dict[str, float] = field(default_factory=dict)
    night_mmr_per_symbol: dict[str, float] = field(default_factory=dict)
    enable_pre_trade_liq_projection: bool = False

    # ==========================================================================
    # CFM (Coinbase Financial Markets) Futures Risk Parameters
    # ==========================================================================
    cfm_max_leverage: int = 5
    cfm_min_liquidation_buffer_pct: float = 0.15  # 15% buffer before liquidation
    cfm_max_exposure_pct: float = 0.8  # Max 80% of equity in CFM positions
    cfm_max_position_size_pct: float = 0.25  # Max 25% of equity per CFM position

    # CFM-specific per-symbol limits
    cfm_leverage_max_per_symbol: dict[str, int] = field(default_factory=dict)
    cfm_max_notional_per_symbol: dict[str, Decimal] = field(default_factory=dict)

    # CFM day/night leverage caps (for overnight margin requirements)
    cfm_day_leverage_max_per_symbol: dict[str, int] = field(default_factory=dict)
    cfm_night_leverage_max_per_symbol: dict[str, int] = field(default_factory=dict)

    # ==========================================================================
    # API Health Guard Thresholds
    # ==========================================================================
    api_error_rate_threshold: float = 0.2  # 20% error rate triggers guard
    api_rate_limit_usage_threshold: float = 0.9  # 90% rate limit usage triggers guard

    # ==========================================================================
    # Graceful Degradation Settings
    # ==========================================================================
    # API health trip: pause duration before allowing new entries
    api_health_cooldown_seconds: int = 300  # 5 minutes

    # Mark staleness: pause symbol duration when mark price is stale
    mark_staleness_cooldown_seconds: int = 120  # 2 minutes
    mark_staleness_threshold_seconds: float = 30.0  # Max allowed mark age before stale
    mark_staleness_allow_reduce_only: bool = True  # Allow reduce-only during mark staleness

    # Slippage guard: pause symbol after repeated failures
    slippage_failure_pause_after: int = 3  # Pause after N consecutive failures
    slippage_pause_seconds: int = 60  # Pause duration per symbol

    # Validation infrastructure failure: pause all trading
    validation_failure_cooldown_seconds: int = 180  # 3 minutes

    # Order preview failures: disable preview after repeated failures
    preview_failure_disable_after: int = 5  # Disable preview after N failures

    # Broker read failures (positions/equity): pause all trading
    broker_outage_max_failures: int = 3  # Max consecutive failures before pause
    broker_outage_cooldown_seconds: int = 120  # Pause duration

    # Order audit alerts
    unfilled_order_alert_seconds: int = 300  # Emit alert for open orders older than this

    # ==========================================================================
    # WebSocket Health Monitoring
    # ==========================================================================
    ws_health_interval_seconds: int = 5  # How often to check WS health
    ws_message_stale_seconds: int = 15  # Max age of last message before stale
    ws_heartbeat_stale_seconds: int = 30  # Max age of last heartbeat before stale
    ws_reconnect_pause_seconds: int = 30  # Pause duration after WS reconnect

    @classmethod
    def from_env(cls) -> "RiskConfig":
        """Load risk configuration from environment variables.

        Uses RISK_* prefixed names (e.g., RISK_MAX_LEVERAGE).
        """

        def _get_env(key: str, default: str) -> str:
            """Get env var from RISK_ prefixed key."""
            return os.getenv(f"RISK_{key}", default)

        return cls(
            max_leverage=int(_get_env("MAX_LEVERAGE", "5")),
            daily_loss_limit_pct=float(_get_env("DAILY_LOSS_LIMIT_PCT", "0.05")),
            max_exposure_pct=float(_get_env("MAX_EXPOSURE_PCT", "0.8")),
            max_position_pct_per_symbol=float(_get_env("MAX_POSITION_PCT_PER_SYMBOL", "0.2")),
            min_liquidation_buffer_pct=float(_get_env("MIN_LIQUIDATION_BUFFER_PCT", "0.1")),
            slippage_guard_bps=int(_get_env("SLIPPAGE_GUARD_BPS", "50")),
            kill_switch_enabled=kill_switch_env_engaged(),
            reduce_only_mode=_get_env("REDUCE_ONLY_MODE", "0") == "1",
            # CFM-specific parameters
            cfm_max_leverage=int(os.getenv("CFM_MAX_LEVERAGE", "5")),
            cfm_min_liquidation_buffer_pct=float(
                os.getenv("CFM_MIN_LIQUIDATION_BUFFER_PCT", "0.15")
            ),
            cfm_max_exposure_pct=float(os.getenv("CFM_MAX_EXPOSURE_PCT", "0.8")),
            cfm_max_position_size_pct=float(os.getenv("CFM_MAX_POSITION_SIZE_PCT", "0.25")),
            # API health guard thresholds
            api_error_rate_threshold=float(_get_env("API_ERROR_RATE_THRESHOLD", "0.2")),
            api_rate_limit_usage_threshold=float(_get_env("API_RATE_LIMIT_USAGE_THRESHOLD", "0.9")),
            # Graceful degradation settings
            api_health_cooldown_seconds=int(_get_env("API_HEALTH_COOLDOWN_SECONDS", "300")),
            mark_staleness_cooldown_seconds=int(_get_env("MARK_STALENESS_COOLDOWN_SECONDS", "120")),
            mark_staleness_threshold_seconds=float(
                _get_env("MARK_STALENESS_THRESHOLD_SECONDS", "30")
            ),
            mark_staleness_allow_reduce_only=_get_env("MARK_STALENESS_ALLOW_REDUCE_ONLY", "1")
            == "1",
            slippage_failure_pause_after=int(_get_env("SLIPPAGE_FAILURE_PAUSE_AFTER", "3")),
            slippage_pause_seconds=int(_get_env("SLIPPAGE_PAUSE_SECONDS", "60")),
            validation_failure_cooldown_seconds=int(
                _get_env("VALIDATION_FAILURE_COOLDOWN_SECONDS", "180")
            ),
            preview_failure_disable_after=int(_get_env("PREVIEW_FAILURE_DISABLE_AFTER", "5")),
            broker_outage_max_failures=int(_get_env("BROKER_OUTAGE_MAX_FAILURES", "3")),
            broker_outage_cooldown_seconds=int(_get_env("BROKER_OUTAGE_COOLDOWN_SECONDS", "120")),
            unfilled_order_alert_seconds=int(_get_env("UNFILLED_ORDER_ALERT_SECONDS", "300")),
            # WebSocket health monitoring
            ws_health_interval_seconds=int(_get_env("WS_HEALTH_INTERVAL_SECONDS", "5")),
            ws_message_stale_seconds=int(_get_env("WS_MESSAGE_STALE_SECONDS", "15")),
            ws_heartbeat_stale_seconds=int(_get_env("WS_HEARTBEAT_STALE_SECONDS", "30")),
            ws_reconnect_pause_seconds=int(_get_env("WS_RECONNECT_PAUSE_SECONDS", "30")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration to dictionary with Decimal handling."""

        def _convert(obj: Any) -> Any:
            if isinstance(obj, Decimal):
                return str(obj)
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            return obj

        return {k: _convert(v) for k, v in asdict(self).items()}

    @classmethod
    def _from_mapping(cls, data: dict[str, Any]) -> "RiskConfig":
        if isinstance(data.get("max_notional_per_symbol"), dict):
            data["max_notional_per_symbol"] = {
                k: Decimal(str(v)) for k, v in data["max_notional_per_symbol"].items()
            }

        # CFM-specific Decimal conversion
        if isinstance(data.get("cfm_max_notional_per_symbol"), dict):
            data["cfm_max_notional_per_symbol"] = {
                k: Decimal(str(v)) for k, v in data["cfm_max_notional_per_symbol"].items()
            }

        return cls(**data)

    @classmethod
    def from_json(cls, path: str) -> "RiskConfig":
        """Load configuration from JSON file."""
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Risk config JSON must be an object")
        return cls._from_mapping(data)

    @classmethod
    def from_yaml(cls, path: str) -> "RiskConfig":
        """Load configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("Risk config YAML must be a mapping")
        return cls._from_mapping(data)

    @classmethod
    def from_file(cls, path: str) -> "RiskConfig":
        """Load configuration from JSON or YAML based on file extension."""
        suffix = Path(path).suffix.lower()
        if suffix in {".yaml", ".yml"}:
            return cls.from_yaml(path)
        return cls.from_json(path)


__all__ = ["RiskConfig", "RISK_CONFIG_ENV_KEYS", "RISK_CONFIG_ENV_ALIASES"]
