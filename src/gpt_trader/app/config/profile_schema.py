"""Profile configuration schema data models.

Pure data contracts for a trading profile: the typed section dataclasses
(trading/strategy/risk/execution/session/monitoring) and the aggregate
:class:`ProfileSchema` with its YAML parsing. The loader that reads YAML files,
resolves overrides, and builds a ``BotConfig`` lives in
:mod:`gpt_trader.app.config.profile_loader`; keeping the schema here lets it be
imported without the loader machinery.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from typing import Any

_SESSION_TIME_PATTERN = re.compile(r"\d{2}:\d{2}(?::\d{2})?\Z")


class ProfileValidationError(ValueError):
    """Raised when profile YAML contains invalid configuration values."""


@dataclass
class TradingConfig:
    """Trading section of profile configuration."""

    symbols: list[str] = field(default_factory=lambda: ["BTC-USD"])
    mode: str = "normal"  # normal | reduce_only | paper
    interval: int = 60  # seconds


@dataclass
class StrategyConfig:
    """Strategy section of profile configuration."""

    type: str = "baseline"  # baseline | mean_reversion
    short_ma_period: int = 5
    long_ma_period: int = 20
    rsi_period: int = 14
    rsi_overbought: int = 70
    rsi_oversold: int = 30


@dataclass
class RiskConfig:
    """Risk management section of profile configuration.

    Risk-appetite fields (daily loss limit, exposure cap, leverage caps)
    are not profile settings: they derive from the canonical RiskBudget at
    engine startup (#1120). Profiles keep sizing and the shorts preference,
    which routes to the per-strategy configs.
    """

    max_position_size: Decimal = field(default_factory=lambda: Decimal("10000"))
    position_fraction: Decimal = field(default_factory=lambda: Decimal("0.1"))
    enable_shorts: bool = False
    stop_loss_pct: Decimal = field(default_factory=lambda: Decimal("0.02"))
    take_profit_pct: Decimal = field(default_factory=lambda: Decimal("0.04"))


@dataclass
class ExecutionConfig:
    """Execution section of profile configuration."""

    time_in_force: str = "GTC"  # GTC | IOC | FOK
    dry_run: bool = False
    mock_broker: bool = False
    mock_fills: bool = False
    use_limit_orders: bool = False
    market_order_fallback: bool = True
    # Stage 1 human-approved loop (default OFF): route live strategy decisions
    # into the approval-gated trade-idea workflow instead of submitting orders.
    strategy_signal_proposals: bool = False
    # Event-driven paper lane (default OFF): carry each proposed idea through
    # the risk kernel to paper execution in-process, per event (#1191).
    # Implies proposal routing; the engine never submits live orders.
    event_driven_paper_lane: bool = False
    # Stage 2 derivation seam (default ON): seed runtime risk limits from the
    # active RiskBudget version at engine startup (#1120).
    risk_budget_runtime_seed: bool = True


@dataclass
class SessionConfig:
    """Session timing section of profile configuration."""

    start_time: time | None = None
    end_time: time | None = None
    trading_days: list[str] = field(
        default_factory=lambda: ["monday", "tuesday", "wednesday", "thursday", "friday"]
    )


def _parse_session_time(
    session_data: Mapping[str, Any], field_name: str, profile_name: str
) -> time | None:
    """Parse an optional session time while preserving explicit YAML nulls."""
    if field_name not in session_data:
        return None

    value = session_data[field_name]
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if not isinstance(value, str) or not _SESSION_TIME_PATTERN.fullmatch(value):
        raise ProfileValidationError(
            f"session.{field_name} for profile '{profile_name}' must be a valid "
            f"HH:MM or HH:MM:SS time string; got {value!r}"
        )
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError(
            f"session.{field_name} for profile '{profile_name}' must be a valid "
            f"HH:MM or HH:MM:SS time string; got {value!r}"
        ) from exc


@dataclass
class MonitoringConfig:
    """Monitoring section of profile configuration."""

    log_level: str = "INFO"
    update_interval: int = 60  # seconds
    status_enabled: bool = True


@dataclass
class ProfileSchema:
    """Complete profile schema with all configuration sections.

    This dataclass represents a unified profile configuration that can be:
    - Loaded from YAML files
    - Used as hardcoded defaults
    - Merged with environment overrides
    """

    profile_name: str
    environment: str = "development"
    description: str = ""

    trading: TradingConfig = field(default_factory=TradingConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    @classmethod
    def from_yaml(cls, data: dict[str, Any], profile_name: str) -> ProfileSchema:
        """Create ProfileSchema from parsed YAML data."""
        trading_data = data.get("trading", {})
        strategy_data = data.get("strategy", {})
        risk_data = data.get("risk_management", {})
        execution_data = data.get("execution", {})
        if not execution_data:
            execution_data = trading_data.get("execution", {})
        session_data = data.get("session", {})
        monitoring_data = data.get("monitoring", {})

        # Parse trading config
        trading = TradingConfig(
            symbols=trading_data.get("symbols", ["BTC-USD"]),
            mode=trading_data.get("mode", "normal"),
            interval=int(trading_data.get("interval", 60)),
        )

        # Parse strategy config
        strategy = StrategyConfig(
            type=strategy_data.get("type", "baseline"),
            short_ma_period=int(strategy_data.get("short_ma_period", 5)),
            long_ma_period=int(strategy_data.get("long_ma_period", 20)),
            rsi_period=int(strategy_data.get("rsi_period", 14)),
            rsi_overbought=int(strategy_data.get("rsi_overbought", 70)),
            rsi_oversold=int(strategy_data.get("rsi_oversold", 30)),
        )

        # Parse risk config (retired appetite keys in YAML are ignored; the
        # RiskBudget seam owns them — #1120)
        risk = RiskConfig(
            max_position_size=Decimal(str(risk_data.get("max_position_size", 10000))),
            position_fraction=Decimal(str(risk_data.get("position_fraction", "0.1"))),
            enable_shorts=risk_data.get("enable_shorts", False),
            stop_loss_pct=Decimal(str(risk_data.get("stop_loss_pct", "0.02"))),
            take_profit_pct=Decimal(str(risk_data.get("take_profit_pct", "0.04"))),
        )

        # Parse execution config
        execution = ExecutionConfig(
            time_in_force=execution_data.get("time_in_force", "GTC"),
            dry_run=execution_data.get("dry_run", False),
            mock_broker=execution_data.get("mock_broker", False),
            mock_fills=execution_data.get("mock_fills", False),
            use_limit_orders=execution_data.get("use_limit_orders", False),
            market_order_fallback=execution_data.get("market_order_fallback", True),
            strategy_signal_proposals=execution_data.get("strategy_signal_proposals", False),
            event_driven_paper_lane=execution_data.get("event_driven_paper_lane", False),
            risk_budget_runtime_seed=execution_data.get("risk_budget_runtime_seed", True),
        )

        # Parse session config
        start_time = _parse_session_time(session_data, "start_time", profile_name)
        end_time = _parse_session_time(session_data, "end_time", profile_name)

        session = SessionConfig(
            start_time=start_time,
            end_time=end_time,
            trading_days=session_data.get(
                "trading_days",
                ["monday", "tuesday", "wednesday", "thursday", "friday"],
            ),
        )

        # Parse monitoring config
        metrics_data = monitoring_data.get("metrics", {})
        monitoring = MonitoringConfig(
            log_level=monitoring_data.get("log_level", "INFO"),
            update_interval=int(metrics_data.get("interval_seconds", 60)),
            status_enabled=monitoring_data.get("status_enabled", True),
        )

        return cls(
            profile_name=data.get("profile_name", profile_name),
            environment=data.get("environment", "development"),
            description=data.get("description", ""),
            trading=trading,
            strategy=strategy,
            risk=risk,
            execution=execution,
            session=session,
            monitoring=monitoring,
        )
