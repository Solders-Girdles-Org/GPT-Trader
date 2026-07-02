"""Tests for EnsembleOrchestrator decision-making behavior."""

from decimal import Decimal

import pytest

from gpt_trader.features.intelligence.ensemble.models import EnsembleConfig
from gpt_trader.features.intelligence.ensemble.orchestrator import EnsembleOrchestrator
from gpt_trader.features.intelligence.regime import RegimeConfig, RegimeType
from gpt_trader.features.live_trade.strategies.baseline import Action

from .orchestrator_test_base import MockStrategy


class TestEnsembleOrchestrator:
    """Test EnsembleOrchestrator."""

    @pytest.fixture
    def mock_strategies(self) -> dict[str, MockStrategy]:
        """Create mock strategies."""
        return {
            "baseline": MockStrategy("baseline", Action.BUY, 0.7),
            "mean_reversion": MockStrategy("mean_reversion", Action.BUY, 0.8),
        }

    @pytest.fixture
    def config(self) -> EnsembleConfig:
        """Create ensemble config."""
        return EnsembleConfig(
            voting_method="confidence_leader",
            base_weights={"baseline": 0.5, "mean_reversion": 0.5},
            min_ensemble_confidence=0.3,
        )

    @pytest.fixture
    def regime_config(self) -> RegimeConfig:
        """Create fast regime config for testing."""
        return RegimeConfig(
            short_ema_period=5,
            long_ema_period=10,
            min_regime_ticks=2,
        )

    @pytest.fixture
    def orchestrator(
        self,
        mock_strategies: dict[str, MockStrategy],
        config: EnsembleConfig,
        regime_config: RegimeConfig,
    ) -> EnsembleOrchestrator:
        """Create orchestrator with mock strategies."""
        return EnsembleOrchestrator(
            strategies=mock_strategies,  # type: ignore[arg-type]
            config=config,
            regime_config=regime_config,
        )

    def test_decide_calls_all_strategies(
        self,
        orchestrator: EnsembleOrchestrator,
        mock_strategies: dict[str, MockStrategy],
    ) -> None:
        """Test that decide() calls all child strategies."""
        orchestrator.decide(
            symbol="BTC-USD",
            current_mark=Decimal("50000"),
            position_state=None,
            recent_marks=[Decimal("50000")],
            equity=Decimal("10000"),
            product=None,
        )

        for strategy in mock_strategies.values():
            assert strategy.call_count == 1

    def test_decide_returns_aggregated_decision(self, orchestrator: EnsembleOrchestrator) -> None:
        """Test that decide() returns aggregated decision."""
        for i in range(20):
            orchestrator.decide(
                symbol="BTC-USD",
                current_mark=Decimal(str(50000 + i * 10)),
                position_state=None,
                recent_marks=[Decimal(str(50000 + i * 10))],
                equity=Decimal("10000"),
                product=None,
            )

        decision = orchestrator.decide(
            symbol="BTC-USD",
            current_mark=Decimal("50200"),
            position_state=None,
            recent_marks=[Decimal("50200")],
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.BUY
        assert decision.confidence > 0.3

    def test_decide_includes_ensemble_metadata(self, orchestrator: EnsembleOrchestrator) -> None:
        """Test that decision includes ensemble metadata."""
        decision = orchestrator.decide(
            symbol="BTC-USD",
            current_mark=Decimal("50000"),
            position_state=None,
            recent_marks=[Decimal("50000")],
            equity=Decimal("10000"),
            product=None,
        )

        assert "ensemble" in decision.indicators
        ensemble_data = decision.indicators["ensemble"]
        assert "regime" in ensemble_data
        assert "weights" in ensemble_data
        assert "votes" in ensemble_data
        assert "voting_method" in ensemble_data

    def test_unanimous_agreement(self, orchestrator: EnsembleOrchestrator) -> None:
        """Test unanimous agreement from strategies."""
        decision = orchestrator.decide(
            symbol="BTC-USD",
            current_mark=Decimal("50000"),
            position_state=None,
            recent_marks=[Decimal("50000")],
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.BUY

    def test_disagreement_follows_leader(
        self,
        config: EnsembleConfig,
        regime_config: RegimeConfig,
    ) -> None:
        """Test that disagreement follows highest confidence."""
        strategies = {
            "high_conf": MockStrategy("high_conf", Action.BUY, 0.9),
            "low_conf": MockStrategy("low_conf", Action.SELL, 0.3),
        }

        orchestrator = EnsembleOrchestrator(
            strategies=strategies,  # type: ignore[arg-type]
            config=config,
            regime_config=regime_config,
        )

        decision = orchestrator.decide(
            symbol="BTC-USD",
            current_mark=Decimal("50000"),
            position_state=None,
            recent_marks=[Decimal("50000")],
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.BUY

    def test_low_confidence_returns_hold(
        self,
        config: EnsembleConfig,
        regime_config: RegimeConfig,
    ) -> None:
        """Test that low ensemble confidence returns HOLD."""
        config.min_ensemble_confidence = 0.95  # Very high threshold

        strategies = {
            "a": MockStrategy("a", Action.BUY, 0.3),
            "b": MockStrategy("b", Action.SELL, 0.3),
        }

        orchestrator = EnsembleOrchestrator(
            strategies=strategies,  # type: ignore[arg-type]
            config=config,
            regime_config=regime_config,
        )

        decision = orchestrator.decide(
            symbol="BTC-USD",
            current_mark=Decimal("50000"),
            position_state=None,
            recent_marks=[Decimal("50000")],
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.HOLD

    def test_get_regime(self, orchestrator: EnsembleOrchestrator) -> None:
        """Test get_regime returns current regime."""
        state = orchestrator.get_regime("BTC-USD")
        assert state.regime == RegimeType.UNKNOWN

        for i in range(30):
            orchestrator.decide(
                symbol="BTC-USD",
                current_mark=Decimal(str(50000 + i * 100)),
                position_state=None,
                recent_marks=[Decimal(str(50000 + i * 100))],
                equity=Decimal("10000"),
                product=None,
            )

        state = orchestrator.get_regime("BTC-USD")
        assert state is not None

    def test_get_strategy_weights(self, orchestrator: EnsembleOrchestrator) -> None:
        """Test get_strategy_weights returns current weights."""
        weights = orchestrator.get_strategy_weights("BTC-USD")

        assert "baseline" in weights
        assert "mean_reversion" in weights
        assert abs(sum(weights.values()) - 1.0) < 0.001
