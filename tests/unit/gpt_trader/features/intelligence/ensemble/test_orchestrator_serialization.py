"""Tests for EnsembleOrchestrator state serialization."""

from decimal import Decimal

import pytest

from gpt_trader.features.intelligence.ensemble.orchestrator import EnsembleOrchestrator
from gpt_trader.features.live_trade.strategies.baseline import Action

from .orchestrator_test_base import MockStrategy


class TestSerialization:
    """Test state serialization."""

    @pytest.fixture
    def orchestrator(self) -> EnsembleOrchestrator:
        """Create orchestrator for serialization tests."""
        strategies = {
            "baseline": MockStrategy("baseline", Action.BUY, 0.7),
        }
        return EnsembleOrchestrator(strategies=strategies)  # type: ignore[arg-type]

    def test_serialize_state(self, orchestrator: EnsembleOrchestrator) -> None:
        """Test state serialization."""
        for i in range(30):
            orchestrator.decide(
                symbol="BTC-USD",
                current_mark=Decimal(str(50000 + i * 50)),
                position_state=None,
                recent_marks=[Decimal(str(50000 + i * 50))],
                equity=Decimal("10000"),
                product=None,
            )

        state = orchestrator.serialize_state()

        assert "regime_detector" in state
        assert "strategies" in state
        assert "last_regimes" in state

    def test_deserialize_state(self, orchestrator: EnsembleOrchestrator) -> None:
        """Test state restoration."""
        for i in range(30):
            orchestrator.decide(
                symbol="BTC-USD",
                current_mark=Decimal(str(50000 + i * 50)),
                position_state=None,
                recent_marks=[Decimal(str(50000 + i * 50))],
                equity=Decimal("10000"),
                product=None,
            )

        serialized = orchestrator.serialize_state()

        new_strategies = {"baseline": MockStrategy("baseline", Action.BUY, 0.7)}
        new_orchestrator = EnsembleOrchestrator(strategies=new_strategies)  # type: ignore[arg-type]

        new_orchestrator.deserialize_state(serialized)

        assert new_orchestrator.regime_detector is not None
