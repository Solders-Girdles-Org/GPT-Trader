"""Tests for SpreadSignal."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.live_trade.signals.protocol import StrategyContext
from gpt_trader.features.live_trade.signals.spread import (
    SpreadSignal,
    SpreadSignalConfig,
)
from gpt_trader.features.live_trade.signals.types import SignalType
from gpt_trader.features.live_trade.strategies.base import MarketDataContext


class MockDepthSnapshot:
    """Mock orderbook depth snapshot with spread."""

    def __init__(self, spread_bps: float) -> None:
        self.spread_bps = spread_bps


class TestSpreadSignalConfig:
    """Tests for SpreadSignalConfig."""

    def test_default_values(self) -> None:
        """Test default config values."""
        config = SpreadSignalConfig()
        assert config.tight_spread_bps == 5.0
        assert config.normal_spread_bps == 15.0
        assert config.wide_spread_bps == 30.0

    def test_custom_values(self) -> None:
        """Test custom config values."""
        config = SpreadSignalConfig(
            tight_spread_bps=3.0,
            normal_spread_bps=10.0,
            wide_spread_bps=25.0,
        )
        assert config.tight_spread_bps == 3.0
        assert config.normal_spread_bps == 10.0


class TestSpreadSignal:
    """Tests for SpreadSignal."""

    @pytest.fixture
    def signal(self) -> SpreadSignal:
        """Create a signal with default config."""
        return SpreadSignal()

    @pytest.fixture
    def base_context(self) -> StrategyContext:
        """Create a basic strategy context."""
        return StrategyContext(
            symbol="BTC-USD",
            current_mark=Decimal("50000"),
            position_state=None,
            recent_marks=[Decimal("49900"), Decimal("49950"), Decimal("50000")],
            equity=Decimal("10000"),
            product=None,
            market_data=None,
        )

    def test_no_market_data_returns_neutral(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that missing market_data returns neutral signal."""
        result = signal.generate(base_context)

        assert result.name == "spread_quality"
        assert result.type == SignalType.MICROSTRUCTURE
        assert result.strength == 0.0  # Spread never indicates direction
        assert result.confidence == 0.0
        assert result.metadata["reason"] == "no_market_data"

    def test_strength_is_always_zero(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that spread signal strength is always zero (non-directional)."""
        base_context.market_data = MarketDataContext(spread_bps=Decimal("10"))
        result = signal.generate(base_context)

        assert result.strength == 0.0  # Spread doesn't predict direction

    def test_tight_spread_high_confidence(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that tight spread results in high confidence."""
        base_context.market_data = MarketDataContext(spread_bps=Decimal("2"))
        result = signal.generate(base_context)

        assert result.confidence >= 0.8
        assert result.metadata["quality"] == "tight"
        assert result.metadata["spread_bps"] == 2.0

    def test_normal_spread_medium_confidence(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that normal spread results in medium confidence."""
        base_context.market_data = MarketDataContext(spread_bps=Decimal("10"))
        result = signal.generate(base_context)

        assert 0.5 <= result.confidence <= 0.8
        assert result.metadata["quality"] == "normal"

    def test_wide_spread_low_confidence(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that wide spread results in low confidence."""
        base_context.market_data = MarketDataContext(spread_bps=Decimal("25"))
        result = signal.generate(base_context)

        assert 0.2 <= result.confidence <= 0.5
        assert result.metadata["quality"] == "wide"

    def test_very_wide_spread_very_low_confidence(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that very wide spread results in very low confidence."""
        base_context.market_data = MarketDataContext(spread_bps=Decimal("50"))
        result = signal.generate(base_context)

        assert result.confidence <= 0.2
        assert result.metadata["quality"] == "very_wide"

    def test_falls_back_to_orderbook_snapshot(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that spread is taken from orderbook if not in market_data."""
        snapshot = MockDepthSnapshot(spread_bps=8.0)
        base_context.market_data = MarketDataContext(spread_bps=None, orderbook_snapshot=snapshot)
        result = signal.generate(base_context)

        assert result.metadata["spread_bps"] == 8.0
        assert result.metadata["quality"] == "normal"

    def test_no_spread_returns_neutral(
        self, signal: SpreadSignal, base_context: StrategyContext
    ) -> None:
        """Test that missing spread data returns neutral signal."""
        snapshot_no_spread = MagicMock()
        snapshot_no_spread.spread_bps = None
        base_context.market_data = MarketDataContext(
            spread_bps=None, orderbook_snapshot=snapshot_no_spread
        )
        result = signal.generate(base_context)

        assert result.confidence == 0.0
        assert result.metadata["reason"] == "no_spread_data"

    def test_confidence_scaling(self, signal: SpreadSignal, base_context: StrategyContext) -> None:
        """Test that confidence scales appropriately across spread ranges."""
        spreads_and_expected = [
            (1.0, "tight", 0.8),  # Very tight
            (5.0, "tight", 0.8),  # At tight threshold
            (10.0, "normal", 0.5),  # Normal range
            (15.0, "normal", 0.5),  # At normal threshold
            (22.5, "wide", 0.35),  # Wide range
            (30.0, "wide", 0.2),  # At wide threshold
            (50.0, "very_wide", 0.1),  # Very wide
        ]

        for spread, expected_quality, min_expected_conf in spreads_and_expected:
            base_context.market_data = MarketDataContext(spread_bps=Decimal(str(spread)))
            result = signal.generate(base_context)

            assert result.metadata["quality"] == expected_quality, (
                f"Spread {spread} should be {expected_quality}"
            )

    def test_custom_thresholds(self, base_context: StrategyContext) -> None:
        """Test signal with custom thresholds."""
        custom_signal = SpreadSignal(
            SpreadSignalConfig(
                tight_spread_bps=2.0,
                normal_spread_bps=8.0,
                wide_spread_bps=15.0,
            )
        )

        # 5 bps is tight with defaults but normal with custom
        base_context.market_data = MarketDataContext(spread_bps=Decimal("5"))
        result = custom_signal.generate(base_context)

        assert result.metadata["quality"] == "normal"
        assert result.metadata["tight_threshold"] == 2.0
        assert result.metadata["wide_threshold"] == 15.0
