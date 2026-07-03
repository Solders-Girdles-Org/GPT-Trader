"""Risk and validation sub-container for ApplicationContainer.

This container manages risk and validation-related dependencies:
- LiveRiskManager (leverage limits, loss limits, exposure caps)
- ValidationFailureTracker (consecutive failure tracking with escalation)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gpt_trader.app.config import BotConfig

if TYPE_CHECKING:
    from gpt_trader.app.risk_budget_seed import RiskBudgetRuntimeSeed
    from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker
    from gpt_trader.features.live_trade.risk.manager import LiveRiskManager
    from gpt_trader.persistence.event_store import EventStore


class RiskValidationContainer:
    """Container for risk and validation-related dependencies.

    Lazily initializes LiveRiskManager and ValidationFailureTracker.

    Args:
        config: Bot configuration.
        event_store_provider: Callable returning the EventStore instance.
            This is a callable (not the instance) to support lazy resolution
            and avoid initialization order issues.
        risk_budget_seed: Runtime risk-appetite values derived from the active
            RiskBudget version at startup (docs/decisions/
            canonical-risk-limit-vocabulary.md). None (the default) keeps the
            pre-seam behavior: appetite fields come from BotConfig.risk.
    """

    def __init__(
        self,
        config: BotConfig,
        event_store_provider: Callable[[], EventStore],
        risk_budget_seed: RiskBudgetRuntimeSeed | None = None,
    ):
        self._config = config
        self._event_store_provider = event_store_provider
        self._risk_budget_seed = risk_budget_seed

        self._risk_manager: LiveRiskManager | None = None
        self._validation_failure_tracker: ValidationFailureTracker | None = None

    @property
    def risk_manager(self) -> LiveRiskManager:
        """Get or create the risk manager instance.

        Creates a LiveRiskManager configured from BotConfig.risk settings.
        The manager enforces leverage limits, daily loss limits, position
        exposure caps, and other risk controls.
        """
        if self._risk_manager is None:
            from gpt_trader.features.live_trade.risk.config import RiskConfig
            from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

            # Adapt BotConfig.risk (BotRiskConfig) to RiskConfig
            bot_risk = self._config.risk

            # Derive kill_switch_enabled from active strategy config
            if self._config.strategy_type == "mean_reversion":
                kill_switch = self._config.mean_reversion.kill_switch_enabled
            else:
                # baseline, ensemble, or any other type uses strategy config
                kill_switch = getattr(self._config.strategy, "kill_switch_enabled", False)

            risk_config_kwargs: dict[str, Any] = {
                "max_leverage": bot_risk.max_leverage,
                "daily_loss_limit_pct": bot_risk.daily_loss_limit_pct,
                "max_position_pct_per_symbol": float(bot_risk.position_fraction),
                # Map other relevant fields
                "kill_switch_enabled": kill_switch,
                "reduce_only_mode": self._config.reduce_only_mode,
            }
            seed = self._risk_budget_seed
            if seed is not None:
                risk_config_kwargs["daily_loss_limit_pct"] = seed.daily_loss_limit_pct
                risk_config_kwargs["max_exposure_pct"] = seed.max_exposure_pct
                if not seed.allow_futures_leverage:
                    # Permission gates magnitude: without futures leverage the
                    # effective CFM cap is 1x regardless of configured caps.
                    risk_config_kwargs["cfm_max_leverage"] = 1
            risk_config = RiskConfig(**risk_config_kwargs)

            profile = getattr(self._config, "profile", None)
            profile_value = (
                profile.value if profile is not None and hasattr(profile, "value") else profile
            )
            state_file = None
            if profile_value:
                runtime_root = Path(getattr(self._config, "runtime_root", "."))
                state_file_path = (
                    runtime_root / "runtime_data" / str(profile_value) / "risk_state.json"
                )
                state_file = str(state_file_path)

            event_store = self._event_store_provider()
            self._risk_manager = LiveRiskManager(
                config=risk_config,
                event_store=event_store,
                state_file=state_file,
            )
            if seed is not None:
                from gpt_trader.app.risk_budget_seed import RISK_BUDGET_SEED_EVENT_TYPE

                # Startup telemetry: record which budget version seeded the
                # runtime breaker so the restart-bounded drift window between
                # approval-time and runtime limits stays visible.
                event_store.append(RISK_BUDGET_SEED_EVENT_TYPE, seed.telemetry_payload())
        return self._risk_manager

    @property
    def validation_failure_tracker(self) -> ValidationFailureTracker:
        """Get or create the validation failure tracker.

        The tracker monitors consecutive validation failures and can trigger
        escalation (e.g., reduce-only mode) when thresholds are exceeded.

        Note: The escalation callback is not set here - it should be configured
        by the caller (e.g., TradingEngine) who knows the escalation target.
        """
        if self._validation_failure_tracker is None:
            from gpt_trader.features.live_trade.execution.validation import (
                ValidationFailureTracker as VFT,
            )

            self._validation_failure_tracker = VFT()
        return self._validation_failure_tracker

    def reset_risk_manager(self) -> None:
        """Reset the risk manager, forcing re-creation on next access."""
        self._risk_manager = None

    def reset_validation_failure_tracker(self) -> None:
        """Reset the validation failure tracker, forcing re-creation on next access."""
        self._validation_failure_tracker = None
