"""Tests for PositionSizer sizing and configuration."""

from decimal import Decimal

import pytest

from gpt_trader.features.intelligence.regime.detector import MarketRegimeDetector
from gpt_trader.features.intelligence.regime.models import RegimeConfig
from gpt_trader.features.intelligence.sizing.position_sizer import (
    PositionSizer,
    PositionSizingConfig,
    SizingResult,
)


class TestPositionSizer:
    @pytest.fixture
    def regime_detector(self):
        """Create regime detector with some price history."""
        detector = MarketRegimeDetector(RegimeConfig())

        base_price = Decimal("50000")
        for i in range(100):
            delta = Decimal(str(i % 10 - 5)) * 10
            detector.update("BTC-USD", base_price + delta)

        return detector

    @pytest.fixture
    def sizer(self, regime_detector):
        """Create position sizer with detector."""
        return PositionSizer(regime_detector=regime_detector)

    def test_basic_sizing(self, sizer):
        """Test basic position size calculation."""
        result = sizer.calculate_size(
            symbol="BTC-USD",
            current_price=Decimal("50000"),
            equity=Decimal("10000"),
        )

        assert isinstance(result, SizingResult)
        assert result.position_fraction > 0
        assert result.position_value > 0
        assert result.position_quantity > 0

    def test_position_fraction_limits(self, sizer):
        """Test position fraction respects limits."""
        result = sizer.calculate_size(
            symbol="BTC-USD",
            current_price=Decimal("50000"),
            equity=Decimal("10000"),
            decision_confidence=1.0,
        )

        config = sizer.config
        assert result.position_fraction >= config.min_position_fraction
        assert result.position_fraction <= config.max_position_fraction

    def test_confidence_affects_size(self):
        """Test that higher confidence increases size."""
        config = PositionSizingConfig(
            enable_confidence_scaling=True,
            base_position_fraction=0.10,
            min_position_fraction=0.0,
            regime_scale_factors={"UNKNOWN": 1.0},
        )
        sizer = PositionSizer(config=config)

        result_low = sizer.calculate_size(
            symbol="TEST",
            current_price=Decimal("100"),
            equity=Decimal("10000"),
            decision_confidence=0.3,
        )

        result_high = sizer.calculate_size(
            symbol="TEST",
            current_price=Decimal("100"),
            equity=Decimal("10000"),
            decision_confidence=0.9,
        )

        assert result_high.confidence_factor > result_low.confidence_factor
        assert result_high.position_fraction > result_low.position_fraction

    def test_regime_affects_size(self, regime_detector):
        """Test that regime affects position size."""
        sizer = PositionSizer(
            regime_detector=regime_detector,
            config=PositionSizingConfig(enable_volatility_scaling=False),
        )

        result = sizer.calculate_size(
            symbol="BTC-USD",
            current_price=Decimal("50000"),
            equity=Decimal("10000"),
        )

        assert result.regime_factor > 0

    def test_sizing_without_detector(self):
        """Test sizing works without regime detector."""
        sizer = PositionSizer(regime_detector=None)

        result = sizer.calculate_size(
            symbol="TEST",
            current_price=Decimal("100"),
            equity=Decimal("10000"),
        )

        assert result.position_fraction > 0
        assert result.regime == "UNKNOWN"

    def test_position_value_calculation(self):
        """Test position value is calculated correctly."""
        sizer = PositionSizer(
            config=PositionSizingConfig(
                base_position_fraction=0.05,
                enable_volatility_scaling=False,
                enable_confidence_scaling=False,
            )
        )

        result = sizer.calculate_size(
            symbol="TEST",
            current_price=Decimal("100"),
            equity=Decimal("10000"),
        )

        expected_value = Decimal("10000") * Decimal(str(result.position_fraction))
        assert result.position_value == pytest.approx(expected_value, rel=Decimal("0.01"))

    def test_position_quantity_calculation(self):
        """Test position quantity is calculated correctly."""
        sizer = PositionSizer()

        result = sizer.calculate_size(
            symbol="TEST",
            current_price=Decimal("100"),
            equity=Decimal("10000"),
        )

        expected_quantity = result.position_value / Decimal("100")
        assert result.position_quantity == pytest.approx(expected_quantity, rel=Decimal("0.001"))

    def test_sizing_result_to_dict(self):
        """Test SizingResult serialization."""
        sizer = PositionSizer()

        result = sizer.calculate_size(
            symbol="TEST",
            current_price=Decimal("100"),
            equity=Decimal("10000"),
        )

        data = result.to_dict()

        assert "position_fraction" in data
        assert "factors" in data
        assert "risk" in data
        assert "context" in data
        assert "reasoning" in data


class TestPositionSizingConfig:
    def test_default_config(self):
        """Test default configuration values."""
        config = PositionSizingConfig()

        assert config.base_position_fraction == 0.02
        assert config.max_position_fraction == 0.10
        assert config.min_position_fraction == 0.005
        assert config.enable_volatility_scaling is True
        assert config.enable_kelly_sizing is False

    def test_regime_scale_factors(self):
        """Test regime scale factors are set."""
        config = PositionSizingConfig()

        assert config.regime_scale_factors["CRISIS"] == 0.2
        assert config.regime_scale_factors["BULL_QUIET"] == 1.2

    def test_to_dict(self):
        """Test serialization to dict."""
        config = PositionSizingConfig()
        data = config.to_dict()

        assert "base_position_fraction" in data
        assert "regime_scale_factors" in data
        assert data["base_position_fraction"] == 0.02

    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "base_position_fraction": 0.05,
            "max_position_fraction": 0.15,
            "enable_kelly_sizing": True,
        }

        config = PositionSizingConfig.from_dict(data)

        assert config.base_position_fraction == 0.05
        assert config.max_position_fraction == 0.15
        assert config.enable_kelly_sizing is True
