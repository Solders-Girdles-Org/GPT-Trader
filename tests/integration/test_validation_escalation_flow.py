"""Integration tests for validation failure escalation flow.

Tests the end-to-end safety loop:
1. Validation checks fail repeatedly
2. Failure tracker counts consecutive failures
3. At threshold, escalation callback fires
4. Risk manager activates reduce-only mode
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.core import OrderSide
from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.risk import LiveRiskManager

pytestmark = pytest.mark.integration


class TestValidationEscalationFlow:
    """Integration tests for validation failure escalation."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create a mock broker."""
        broker = MagicMock()
        broker.list_balances.return_value = []
        broker.get_product.return_value = MagicMock()
        return broker

    @pytest.fixture
    def real_risk_manager(self) -> LiveRiskManager:
        """Create a real risk manager for integration testing."""
        manager = LiveRiskManager()
        manager.set_reduce_only_mode(False, reason="test_setup")
        return manager

    @pytest.fixture
    def bot_config(self) -> BotConfig:
        """Create a basic bot config."""
        return BotConfig(symbols=["BTC-USD"])

    @pytest.fixture
    def container(self, bot_config: BotConfig):
        container = ApplicationContainer(bot_config)
        set_application_container(container)
        yield container
        clear_application_container()

    @pytest.fixture
    def engine(
        self,
        mock_broker: MagicMock,
        real_risk_manager: LiveRiskManager,
        bot_config: BotConfig,
        container,
    ):
        """Create TradingEngine with real risk manager."""
        context = CoordinatorContext(
            config=bot_config,
            container=container,
            broker=mock_broker,
            risk_manager=real_risk_manager,
            event_store=container.event_store,
            bot_id="validation_escalation",
        )
        return TradingEngine(context)

    @pytest.fixture
    def validator(self, engine):
        validator = engine._order_validator
        assert validator is not None
        return validator

    def test_escalation_flow_triggers_reduce_only_mode(
        self,
        validator,
        real_risk_manager: LiveRiskManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that 5 consecutive validation failures trigger reduce-only mode."""
        assert not real_risk_manager.is_reduce_only_mode()

        monkeypatch.setattr(
            real_risk_manager,
            "check_mark_staleness",
            MagicMock(side_effect=RuntimeError("Simulated API failure")),
        )
        for i in range(4):
            validator.ensure_mark_is_fresh("BTC-USD")
            assert not real_risk_manager.is_reduce_only_mode(), (
                f"Reduce-only triggered too early at failure {i + 1}"
            )

        validator.ensure_mark_is_fresh("BTC-USD")

        assert real_risk_manager.is_reduce_only_mode()

    def test_successful_validation_resets_failure_counter(
        self,
        validator,
        real_risk_manager: LiveRiskManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that successful validations reset the failure counter."""
        assert not real_risk_manager.is_reduce_only_mode()

        # First 3 failures
        monkeypatch.setattr(
            real_risk_manager,
            "check_mark_staleness",
            MagicMock(side_effect=RuntimeError("API failure")),
        )
        for _ in range(3):
            validator.ensure_mark_is_fresh("BTC-USD")

        # Successful validation resets counter
        monkeypatch.setattr(
            real_risk_manager,
            "check_mark_staleness",
            MagicMock(return_value=False),
        )
        validator.ensure_mark_is_fresh("BTC-USD")

        # 4 more failures (not enough to trigger escalation after reset)
        monkeypatch.setattr(
            real_risk_manager,
            "check_mark_staleness",
            MagicMock(side_effect=RuntimeError("API failure")),
        )
        for _ in range(4):
            validator.ensure_mark_is_fresh("BTC-USD")

        assert not real_risk_manager.is_reduce_only_mode()

    def test_slippage_guard_failures_also_trigger_escalation(
        self,
        validator,
        real_risk_manager: LiveRiskManager,
        mock_broker: MagicMock,
    ) -> None:
        """Test that slippage guard failures also trigger escalation."""
        assert not real_risk_manager.is_reduce_only_mode()

        mock_broker.get_market_snapshot.side_effect = RuntimeError("API failure")

        for _ in range(5):
            validator.enforce_slippage_guard(
                symbol="BTC-USD",
                side=OrderSide.BUY,
                order_quantity=Decimal("1.0"),
                effective_price=Decimal("50000"),
            )

        assert real_risk_manager.is_reduce_only_mode()

    def test_different_check_types_have_independent_counters(
        self,
        validator,
        real_risk_manager: LiveRiskManager,
        mock_broker: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that different check types track failures independently."""
        assert not real_risk_manager.is_reduce_only_mode()

        monkeypatch.setattr(
            real_risk_manager,
            "check_mark_staleness",
            MagicMock(side_effect=RuntimeError("API failure")),
        )
        for _ in range(3):
            validator.ensure_mark_is_fresh("BTC-USD")

        mock_broker.get_market_snapshot.side_effect = RuntimeError("API failure")
        for _ in range(3):
            validator.enforce_slippage_guard(
                symbol="BTC-USD",
                side=OrderSide.BUY,
                order_quantity=Decimal("1.0"),
                effective_price=Decimal("50000"),
            )

        assert not real_risk_manager.is_reduce_only_mode()

    def test_escalation_reason_is_correct(
        self,
        mock_broker: MagicMock,
        bot_config: BotConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that escalation sets the correct reason on risk manager."""
        risk_manager = LiveRiskManager()
        risk_manager.set_reduce_only_mode(False, reason="test_setup")

        container = ApplicationContainer(bot_config)
        set_application_container(container)
        try:
            context = CoordinatorContext(
                config=bot_config,
                container=container,
                broker=mock_broker,
                risk_manager=risk_manager,
                event_store=container.event_store,
                bot_id="validation_escalation",
            )
            engine = TradingEngine(context)
            validator = engine._order_validator
            assert validator is not None

            monkeypatch.setattr(
                risk_manager,
                "check_mark_staleness",
                MagicMock(side_effect=RuntimeError("API failure")),
            )
            for _ in range(5):
                validator.ensure_mark_is_fresh("BTC-USD")
        finally:
            clear_application_container()

        assert risk_manager.is_reduce_only_mode()
        reason = getattr(risk_manager, "_reduce_only_reason", None)
        if reason is not None:
            assert "validation" in reason.lower() or "consecutive" in reason.lower()
