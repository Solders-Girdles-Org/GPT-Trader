"""
Live risk management for perpetuals and CFM futures trading.

This module provides the LiveRiskManager class which enforces:
- Maximum leverage limits (global and per-symbol)
- Day/night leverage caps with configurable time windows
- Daily loss limits with automatic reduce-only mode
- Position exposure limits
- Volatility circuit breakers
- Mark price staleness detection
- Liquidation buffer monitoring
- CFM-specific exposure and margin tracking
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from gpt_trader.core.risk_units import trading_day
from gpt_trader.features.live_trade.risk.manager.models import (
    ExposureState,
    RiskWarning,
    RiskWarningLevel,
    ValidationError,
    VolatilityCheckOutcome,
)
from gpt_trader.features.live_trade.risk.manager.models import (
    RiskValidationError as RiskValidationError,  # re-exported public API
)
from gpt_trader.utilities.datetime_helpers import utc_now

if TYPE_CHECKING:
    from gpt_trader.features.live_trade.risk.config import RiskConfig
    from gpt_trader.persistence.event_store import EventStore


class LiveRiskManager:
    """Manages risk controls for live perpetuals and CFM futures trading.

    Enforces leverage limits, daily loss limits, exposure caps, and circuit breakers.
    Integrates with EventStore for metrics/alerting.
    Supports unified risk tracking across spot and CFM markets.
    """

    def __init__(
        self,
        config: RiskConfig | None = None,
        event_store: EventStore | None = None,
        state_file: str | None = "var/data/risk_state.json",
    ) -> None:
        """
        Initialize the risk manager.

        Args:
            config: Risk configuration with leverage limits, loss limits, etc.
            event_store: Event store for recording risk metrics.
            state_file: Path to JSON file for persisting daily risk state.
        """
        self.config = config
        self.event_store = event_store
        self.state_file = state_file
        self.positions: defaultdict[str, dict[str, Any]] = defaultdict(dict)
        self.last_mark_update: dict[str, float] = {}
        self._reduce_only_mode: bool = False
        self._reduce_only_reason: str = ""
        self._daily_pnl_triggered: bool = False
        self._risk_metrics: list[dict[str, Any]] = []
        self._start_of_day_equity: Decimal | None = None
        self._state_load_error: str | None = None
        self._state_save_error: str | None = None
        # Allows time mocking for tests; defaults to real UTC time
        self._now_provider: Callable[[], datetime] = utc_now

        # CFM-specific tracking
        self._exposure_state = ExposureState()
        self._cfm_reduce_only_mode: bool = False
        self._cfm_reduce_only_reason: str = ""
        self._risk_warnings: list[RiskWarning] = []

        # Load persisted state if available
        if self.state_file:
            self._load_state()

    def _load_state(self) -> None:
        """Load risk state from disk."""
        import json
        import os

        if not self.state_file or not os.path.exists(self.state_file):
            self._state_load_error = None
            return

        try:
            with open(self.state_file) as f:
                payload = f.read()

            state = json.loads(payload)
            if not isinstance(state, dict):
                raise ValueError("Risk state payload is not an object")

            saved_date = state.get("date")
            current_date = trading_day(self._now_provider()).isoformat()

            if saved_date != current_date:
                self._state_load_error = None
                return

            start_of_day_value = state.get("start_of_day_equity")
            if start_of_day_value is not None:
                self._start_of_day_equity = Decimal(str(start_of_day_value))

            self._daily_pnl_triggered = state.get("daily_pnl_triggered", False)
            self._reduce_only_mode = state.get("reduce_only_mode", False)
            self._reduce_only_reason = state.get("reduce_only_reason", "")
            self._cfm_reduce_only_mode = state.get("cfm_reduce_only_mode", False)
            self._cfm_reduce_only_reason = state.get("cfm_reduce_only_reason", "")
            self._state_load_error = None

        except (json.JSONDecodeError, ValueError, TypeError, InvalidOperation) as exc:
            self._handle_state_load_error(exc)
        except Exception as exc:
            self._handle_state_load_error(exc)

    def _save_state(self) -> None:
        """Save risk state to disk."""
        import json

        if not self.state_file:
            return

        self._state_save_error = None
        try:
            # Ensure directory exists
            from pathlib import Path

            Path(self.state_file).parent.mkdir(parents=True, exist_ok=True)

            state = {
                "date": trading_day(self._now_provider()).isoformat(),
                "start_of_day_equity": (
                    str(self._start_of_day_equity) if self._start_of_day_equity else None
                ),
                "daily_pnl_triggered": self._daily_pnl_triggered,
                "reduce_only_mode": self._reduce_only_mode,
                "reduce_only_reason": self._reduce_only_reason,
                "updated_at": time.time(),
            }

            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)

            self._state_save_error = None
        except Exception as e:
            self._handle_state_save_error(e)
        return

    def _handle_state_load_error(self, exc: Exception) -> None:
        """Reset to safe defaults after a state load failure."""
        print(f"Failed to load risk state: {exc}")
        self._reset_state_after_load_failure()
        self._state_load_error = str(exc)

    def _handle_state_save_error(self, exc: Exception) -> None:
        """Track save failures without crashing."""
        print(f"Failed to save risk state: {exc}")
        self._state_save_error = str(exc)

    def _reset_state_after_load_failure(self) -> None:
        """Restore defaults when persisted state cannot be consumed."""
        self._start_of_day_equity = None
        self._daily_pnl_triggered = False
        self._reduce_only_mode = False
        self._reduce_only_reason = ""
        self._cfm_reduce_only_mode = False
        self._cfm_reduce_only_reason = ""

    def check_order(self, order: Any) -> bool:
        """Check if an order is allowed by risk rules.

        This is a quick check for order-level validation.
        For comprehensive pre-trade validation, use pre_trade_validate() instead.

        Returns:
            True if order is allowed, False if blocked by risk rules.
        """
        # Check reduce-only mode (triggered by daily loss limit or other conditions)
        # When in reduce-only mode, only allow orders that reduce position
        if self._reduce_only_mode or self._daily_pnl_triggered:
            reduce_only = False
            if hasattr(order, "reduce_only"):
                reduce_only = order.reduce_only
            elif isinstance(order, dict):
                reduce_only = order.get("reduce_only", False)

            if not reduce_only:
                return False

        return True

    def update_position(self, position: Any) -> None:
        pass

    def check_liquidation_buffer(self, symbol: str, position: Any, equity: Decimal) -> bool:
        if not self.config:
            return False

        try:
            # Handle object or dict
            if isinstance(position, dict):
                liq_price = position.get("liquidation_price")
                mark_price = position.get("mark") or position.get("mark_price")
            else:
                liq_price = getattr(position, "liquidation_price", None)
                mark_price = getattr(position, "mark_price", None) or getattr(
                    position, "mark", None
                )

            if not liq_price or not mark_price:
                return False

            mark_price = Decimal(str(mark_price))
            liq_price = Decimal(str(liq_price))

            if mark_price == 0:
                return False

            buffer_pct = abs(mark_price - liq_price) / mark_price

            if buffer_pct < self.config.min_liquidation_buffer_pct:
                self.positions[symbol]["reduce_only"] = True
                return True

        except (AttributeError, TypeError, ZeroDivisionError, ValueError):
            pass

        return False

    def pre_trade_validate(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        product: Any,
        equity: Decimal,
        current_positions: dict[str, Any],
    ) -> None:
        if not self.config:
            return

        notional = quantity * price

        if equity <= 0:
            return

        leverage = notional / equity

        # 1. Global max leverage check
        if leverage > self.config.max_leverage:
            raise ValidationError(f"Leverage {leverage} exceeds max {self.config.max_leverage}")

        # 2. Day/night leverage cap check (per-symbol)
        is_daytime = self._is_daytime()
        symbol_leverage_cap = self._get_symbol_leverage_cap(symbol, is_daytime)
        if symbol_leverage_cap is not None and leverage > symbol_leverage_cap:
            time_period = "day" if is_daytime else "night"
            raise ValidationError(
                f"Leverage {leverage:.1f}x exceeds {symbol} cap of {symbol_leverage_cap}x ({time_period})"
            )

        # 3. Total exposure validation
        self._validate_total_exposure(symbol, notional, equity, current_positions)

        # 4. MMR projection check (liquidation buffer)
        if getattr(self.config, "enable_pre_trade_liq_projection", False):
            self._validate_mmr_projection(symbol, notional, equity, is_daytime)

    def _is_daytime(self) -> bool:
        """Check if current time is within daytime window."""
        now = self._now_provider()
        current_time_str = now.strftime("%H:%M")

        daytime_start = getattr(self.config, "daytime_start_utc", "09:00")
        daytime_end = getattr(self.config, "daytime_end_utc", "17:00")

        return daytime_start <= current_time_str < daytime_end

    def _get_symbol_leverage_cap(self, symbol: str, is_daytime: bool) -> int | None:
        """Get the leverage cap for a symbol based on time of day."""
        if is_daytime:
            caps = getattr(self.config, "day_leverage_max_per_symbol", {})
        else:
            caps = getattr(self.config, "night_leverage_max_per_symbol", {})

        return caps.get(symbol)

    def _validate_total_exposure(
        self,
        symbol: str,
        new_notional: Decimal,
        equity: Decimal,
        current_positions: dict[str, Any],
    ) -> None:
        """Validate that total exposure doesn't exceed cap."""
        max_exposure_pct = getattr(self.config, "max_exposure_pct", 1.0)

        # Calculate current total exposure
        current_exposure = Decimal("0")
        for pos_symbol, pos_data in current_positions.items():
            if isinstance(pos_data, dict):
                quantity = Decimal(str(pos_data.get("quantity", 0)))
                mark = Decimal(str(pos_data.get("mark", pos_data.get("price", 0))))
                current_exposure += abs(quantity * mark)

        # Add the new position notional
        total_exposure = current_exposure + new_notional
        exposure_pct = total_exposure / equity

        if exposure_pct > Decimal(str(max_exposure_pct)):
            raise ValidationError(
                f"Total exposure {exposure_pct:.2%} would exceed cap of {max_exposure_pct:.0%}"
            )

    def _validate_mmr_projection(
        self, symbol: str, notional: Decimal, equity: Decimal, is_daytime: bool
    ) -> None:
        """Validate that projected liquidation buffer is sufficient."""
        # Get the appropriate MMR based on time of day
        if is_daytime:
            mmr_map = getattr(self.config, "day_mmr_per_symbol", {})
        else:
            mmr_map = getattr(self.config, "night_mmr_per_symbol", {})

        mmr = mmr_map.get(symbol, 0.0)
        if mmr == 0:
            return

        # Calculate projected margin requirement
        margin_required = notional * Decimal(str(mmr))

        # Check against minimum liquidation buffer
        min_buffer = getattr(self.config, "min_liquidation_buffer_pct", 0.0)
        buffer_pct = (equity - margin_required) / equity if equity > 0 else Decimal("0")

        if buffer_pct < Decimal(str(min_buffer)):
            raise ValidationError(
                f"Projected liquidation buffer {buffer_pct:.2%} below minimum {min_buffer:.0%}"
            )

    def track_daily_pnl(
        self, equity: Decimal, positions_pnl: dict[str, dict[str, Decimal]]
    ) -> bool:
        """
        Track daily PnL and check if loss limit is breached.

        Returns:
            True if daily loss limit was triggered, False otherwise.
        """
        if self._start_of_day_equity is None:
            self._start_of_day_equity = equity
            self._save_state()
            return self._daily_pnl_triggered

        if not self.config:
            return self._daily_pnl_triggered

        daily_loss_limit = getattr(self.config, "daily_loss_limit_pct", None)
        if isinstance(daily_loss_limit, bool):
            return self._daily_pnl_triggered
        if isinstance(daily_loss_limit, str) and not daily_loss_limit.strip():
            return self._daily_pnl_triggered
        if not daily_loss_limit:
            return self._daily_pnl_triggered
        try:
            daily_loss_limit_pct = Decimal(str(daily_loss_limit))
        except (InvalidOperation, ValueError, TypeError):
            self._daily_pnl_triggered = True
            self.set_reduce_only_mode(True, reason="daily_loss_limit_invalid")
            return True

        daily_pnl = equity - self._start_of_day_equity
        if self._start_of_day_equity > 0:
            loss_pct = -daily_pnl / self._start_of_day_equity
            if loss_pct >= daily_loss_limit_pct:
                self._daily_pnl_triggered = True
                self.set_reduce_only_mode(True, reason="daily_loss_limit_breached")
                self._save_state()
                return True
        return self._daily_pnl_triggered

    def check_mark_staleness(self, symbol: str) -> bool:
        """
        Check if mark price for symbol is stale.

        Returns:
            True if mark is stale (or missing), False if fresh.
        """
        last_update = self.last_mark_update.get(symbol)
        if last_update is None:
            return True

        staleness_threshold = 30.0  # seconds
        if self.config and hasattr(self.config, "mark_staleness_threshold_seconds"):
            staleness_threshold = float(self.config.mark_staleness_threshold_seconds)
        elif self.config and hasattr(self.config, "mark_staleness_threshold"):
            # Backward-compatible fallback for legacy test/mocked config objects.
            staleness_threshold = float(self.config.mark_staleness_threshold)

        return (time.time() - last_update) > staleness_threshold

    def append_risk_metrics(
        self, equity: Decimal, positions_dict: dict[str, dict[str, Decimal]]
    ) -> None:
        """Append current risk metrics for monitoring."""
        metrics = {
            "timestamp": time.time(),
            "equity": str(equity),
            "positions": {
                k: {kk: str(vv) for kk, vv in v.items()} for k, v in positions_dict.items()
            },
            "reduce_only_mode": self._reduce_only_mode,
        }
        self._risk_metrics.append(metrics)
        # Keep only last 100 metrics
        if len(self._risk_metrics) > 100:
            self._risk_metrics = self._risk_metrics[-100:]

    def check_volatility_circuit_breaker(
        self, symbol: str, closes: list[Decimal]
    ) -> VolatilityCheckOutcome:
        """
        Check if volatility exceeds threshold for the symbol.

        Returns:
            VolatilityCheckOutcome with triggered=True if breaker should trip.
        """
        if not closes or len(closes) < 5:
            return VolatilityCheckOutcome(triggered=False, symbol=symbol)

        if not self.config:
            return VolatilityCheckOutcome(triggered=False, symbol=symbol)

        threshold = getattr(self.config, "volatility_threshold_pct", None)
        if not threshold:
            return VolatilityCheckOutcome(triggered=False, symbol=symbol)

        # Calculate simple volatility as max deviation from mean
        avg_close = sum(closes, Decimal("0")) / len(closes)
        if avg_close == 0:
            return VolatilityCheckOutcome(triggered=False, symbol=symbol)

        max_deviation = max(abs(c - avg_close) for c in closes)
        volatility_pct = max_deviation / avg_close

        if volatility_pct > Decimal(str(threshold)):
            self.set_reduce_only_mode(True, reason=f"volatility_breaker_{symbol}")
            return VolatilityCheckOutcome(
                triggered=True,
                symbol=symbol,
                reason=f"Volatility {volatility_pct:.2%} exceeds threshold {threshold}",
            )

        return VolatilityCheckOutcome(triggered=False, symbol=symbol)

    def set_reduce_only_mode(self, value: bool, reason: str = "") -> None:
        """Set the reduce-only trading mode."""
        self._reduce_only_mode = value
        self._reduce_only_reason = reason
        self._save_state()

    def is_reduce_only_mode(self) -> bool:
        """Check if reduce-only mode is active."""
        return self._reduce_only_mode

    def reset_daily_tracking(self) -> None:
        """Reset daily tracking state (call at start of each trading day)."""
        self._start_of_day_equity = None
        self._daily_pnl_triggered = False
        self._reduce_only_mode = False
        self._reduce_only_reason = ""
        self._cfm_reduce_only_mode = False
        self._cfm_reduce_only_reason = ""
        self._risk_warnings.clear()
        self._save_state()

    # =========================================================================
    # CFM-Specific Risk Management
    # =========================================================================

    def update_exposure(
        self,
        positions: list[Any],
        cfm_balance: Any | None = None,
    ) -> ExposureState:
        """Update exposure tracking from all positions.

        Args:
            positions: List of Position objects with product_type attribute.
            cfm_balance: Optional CFMBalance object with margin info.

        Returns:
            Updated ExposureState.
        """
        spot_exposure = Decimal("0")
        cfm_exposure = Decimal("0")

        for position in positions:
            # Handle both object and dict access
            if isinstance(position, dict):
                quantity = Decimal(str(position.get("quantity", 0)))
                mark_price = Decimal(str(position.get("mark_price") or position.get("mark", 0)))
                product_type = position.get("product_type", "SPOT")
                leverage = int(position.get("leverage", 1))
            else:
                quantity = Decimal(str(getattr(position, "quantity", 0)))
                mark_price = Decimal(
                    str(getattr(position, "mark_price", None) or getattr(position, "mark", 0))
                )
                product_type = getattr(position, "product_type", "SPOT")
                leverage = int(getattr(position, "leverage", 1))

            notional = abs(quantity * mark_price)

            if product_type in ("SPOT",):
                spot_exposure += notional
            elif product_type in ("FUTURE", "PERPETUAL"):
                # For futures, track leveraged exposure
                cfm_exposure += notional * leverage

        self._exposure_state.spot_exposure = spot_exposure
        self._exposure_state.cfm_exposure = cfm_exposure

        # Update CFM margin info if available
        if cfm_balance is not None:
            if isinstance(cfm_balance, dict):
                self._exposure_state.cfm_margin_used = Decimal(
                    str(cfm_balance.get("margin_used") or cfm_balance.get("initial_margin", 0))
                )
                self._exposure_state.cfm_available_margin = Decimal(
                    str(cfm_balance.get("available_margin", 0))
                )
                self._exposure_state.cfm_buying_power = Decimal(
                    str(cfm_balance.get("futures_buying_power", 0))
                )
            else:
                self._exposure_state.cfm_margin_used = Decimal(
                    str(
                        getattr(cfm_balance, "margin_used", None)
                        or getattr(cfm_balance, "initial_margin", 0)
                    )
                )
                self._exposure_state.cfm_available_margin = Decimal(
                    str(getattr(cfm_balance, "available_margin", 0))
                )
                self._exposure_state.cfm_buying_power = Decimal(
                    str(getattr(cfm_balance, "futures_buying_power", 0))
                )

        return self._exposure_state

    def get_exposure_state(self) -> ExposureState:
        """Get current exposure state."""
        return self._exposure_state

    def get_total_exposure(self) -> Decimal:
        """Get total exposure across spot and CFM markets."""
        return self._exposure_state.total_exposure

    def check_cfm_liquidation_buffer(
        self,
        cfm_balance: Any,
        positions: list[Any] | None = None,
    ) -> list[RiskWarning]:
        """Check liquidation buffer for CFM positions.

        Args:
            cfm_balance: CFMBalance object with liquidation buffer info.
            positions: Optional list of CFM positions for per-position checks.

        Returns:
            List of RiskWarning objects if buffer is low.
        """
        warnings: list[RiskWarning] = []

        if cfm_balance is None:
            return warnings

        # Get buffer percentage from CFM balance
        if isinstance(cfm_balance, dict):
            buffer_pct = cfm_balance.get("liquidation_buffer_percentage")
            if buffer_pct is None:
                # Calculate from available margin and total equity
                total_equity = Decimal(str(cfm_balance.get("total_usd_balance", 0)))
                maintenance_margin = Decimal(str(cfm_balance.get("maintenance_margin", 0)))
                if total_equity > 0 and maintenance_margin > 0:
                    buffer_pct = float((total_equity - maintenance_margin) / total_equity)
        else:
            buffer_pct = getattr(cfm_balance, "liquidation_buffer_percentage", None)
            if buffer_pct is None:
                total_equity = Decimal(str(getattr(cfm_balance, "total_usd_balance", 0)))
                maintenance_margin = Decimal(str(getattr(cfm_balance, "maintenance_margin", 0)))
                if total_equity > 0 and maintenance_margin > 0:
                    buffer_pct = float((total_equity - maintenance_margin) / total_equity)

        if buffer_pct is None:
            return warnings

        buffer_pct = float(buffer_pct)

        # Get CFM-specific buffer threshold from config
        min_buffer = 0.15  # Default 15%
        if self.config:
            min_buffer = getattr(self.config, "cfm_min_liquidation_buffer_pct", min_buffer)

        # Check buffer levels
        if buffer_pct < min_buffer:
            severity = (
                RiskWarningLevel.CRITICAL
                if buffer_pct < min_buffer * 0.5
                else RiskWarningLevel.WARNING
            )
            warning = RiskWarning(
                level=severity,
                message=f"CFM liquidation buffer at {buffer_pct:.1%} (min: {min_buffer:.0%})",
                action="REDUCE_POSITION",
                details={
                    "buffer_pct": buffer_pct,
                    "min_buffer_pct": min_buffer,
                },
            )
            warnings.append(warning)

            # Enter CFM reduce-only mode if critical
            if severity == RiskWarningLevel.CRITICAL:
                self._cfm_reduce_only_mode = True
                self._cfm_reduce_only_reason = f"liquidation_buffer_{buffer_pct:.1%}"

        # Store warnings for monitoring
        self._risk_warnings.extend(warnings)

        return warnings

    def check_cfm_exposure_limits(
        self,
        equity: Decimal,
    ) -> list[RiskWarning]:
        """Check if CFM exposure exceeds configured limits.

        Args:
            equity: Total account equity.

        Returns:
            List of RiskWarning objects if limits exceeded.
        """
        warnings: list[RiskWarning] = []

        if equity <= 0:
            return warnings

        cfm_exposure = self._exposure_state.cfm_exposure
        exposure_pct = cfm_exposure / equity

        # Get CFM-specific exposure limit from config
        max_cfm_exposure_pct = 0.8  # Default 80%
        if self.config:
            max_cfm_exposure_pct = getattr(
                self.config, "cfm_max_exposure_pct", max_cfm_exposure_pct
            )

        if exposure_pct > Decimal(str(max_cfm_exposure_pct)):
            warning = RiskWarning(
                level=RiskWarningLevel.WARNING,
                message=f"CFM exposure at {float(exposure_pct):.1%} exceeds limit {max_cfm_exposure_pct:.0%}",
                action="REDUCE_POSITION",
                details={
                    "cfm_exposure": str(cfm_exposure),
                    "exposure_pct": str(exposure_pct),
                    "max_exposure_pct": max_cfm_exposure_pct,
                },
            )
            warnings.append(warning)
            self._risk_warnings.append(warning)

        return warnings

    def validate_cfm_leverage(
        self,
        symbol: str,
        requested_leverage: int,
    ) -> None:
        """Validate that requested leverage is within CFM limits.

        Args:
            symbol: Trading symbol.
            requested_leverage: Requested leverage multiplier.

        Raises:
            ValidationError: If leverage exceeds limits.
        """
        max_leverage = 5  # Default
        if self.config:
            max_leverage = getattr(self.config, "cfm_max_leverage", max_leverage)

        if requested_leverage > max_leverage:
            raise ValidationError(
                f"Requested leverage {requested_leverage}x exceeds CFM limit {max_leverage}x"
            )

        # Check per-symbol leverage caps if configured
        if self.config:
            is_daytime = self._is_daytime()
            symbol_cap = self._get_symbol_leverage_cap(symbol, is_daytime)
            if symbol_cap is not None and requested_leverage > symbol_cap:
                time_period = "day" if is_daytime else "night"
                raise ValidationError(
                    f"Requested leverage {requested_leverage}x exceeds {symbol} "
                    f"cap of {symbol_cap}x ({time_period})"
                )

    def is_cfm_reduce_only_mode(self) -> bool:
        """Check if CFM reduce-only mode is active."""
        return self._cfm_reduce_only_mode

    def set_cfm_reduce_only_mode(self, value: bool, reason: str = "") -> None:
        """Set CFM-specific reduce-only mode."""
        self._cfm_reduce_only_mode = value
        self._cfm_reduce_only_reason = reason
        self._save_state()

    def get_risk_warnings(self) -> list[RiskWarning]:
        """Get all current risk warnings."""
        return self._risk_warnings.copy()

    def clear_risk_warnings(self) -> None:
        """Clear all risk warnings."""
        self._risk_warnings.clear()

    def get_cfm_risk_summary(self) -> dict[str, Any]:
        """Get a summary of CFM-specific risk state.

        Returns:
            Dictionary with CFM risk metrics.
        """
        return {
            "exposure": self._exposure_state.to_payload(),
            "reduce_only_mode": self._cfm_reduce_only_mode,
            "reduce_only_reason": self._cfm_reduce_only_reason,
            "warnings_count": len(self._risk_warnings),
            "warnings": [w.to_payload() for w in self._risk_warnings[-10:]],
        }
