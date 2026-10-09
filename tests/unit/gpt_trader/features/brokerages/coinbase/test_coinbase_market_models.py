"""Coinbase market data models and staleness tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gpt_trader.core import MarketType
from gpt_trader.features.brokerages.coinbase.market_data_features import (
    DepthSnapshot,
    RollingWindow,
    TradeTapeAgg,
)
from gpt_trader.features.brokerages.coinbase.models import APIConfig, to_product
from tests.unit.gpt_trader.features.brokerages.coinbase.minimal_brokerage import (
    MinimalCoinbaseBrokerage,
)

pytestmark = pytest.mark.endpoints


class TestCoinbaseMarketDataModels:
    def test_depth_snapshot_l1_l10_depth_correctness(self) -> None:
        levels = [
            (Decimal("50000"), Decimal("1.0"), "bid"),
            (Decimal("49990"), Decimal("2.0"), "bid"),
            (Decimal("49980"), Decimal("3.0"), "bid"),
            (Decimal("50010"), Decimal("1.5"), "ask"),
            (Decimal("50020"), Decimal("2.5"), "ask"),
            (Decimal("50030"), Decimal("3.5"), "ask"),
        ]
        snapshot = DepthSnapshot(levels)
        assert snapshot.get_l1_depth() == Decimal("1.0")
        assert snapshot.get_l10_depth() == Decimal("13.5")

    def test_depth_snapshot_spread_bps(self) -> None:
        levels = [
            (Decimal("50000"), Decimal("1.0"), "bid"),
            (Decimal("50010"), Decimal("1.0"), "ask"),
        ]
        snapshot = DepthSnapshot(levels)
        assert snapshot.spread_bps == 2.0

    def test_depth_snapshot_mid_price(self) -> None:
        levels = [
            (Decimal("50000"), Decimal("1.0"), "bid"),
            (Decimal("50020"), Decimal("1.0"), "ask"),
        ]
        snapshot = DepthSnapshot(levels)
        assert snapshot.mid == Decimal("50010")

    def test_rolling_window_cleanup_and_stats(self) -> None:
        window = RollingWindow(duration_seconds=10)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        window.add(100.0, base_time)
        window.add(200.0, base_time + timedelta(seconds=5))
        window.add(300.0, base_time + timedelta(seconds=8))
        stats = window.get_stats()
        assert stats["count"] == 3
        assert stats["sum"] == 600.0
        assert stats["avg"] == 200.0

        window.add(400.0, base_time + timedelta(seconds=15))
        stats = window.get_stats()
        assert stats["count"] == 3
        assert stats["sum"] == 900.0
        assert stats["avg"] == 300.0

    def test_trade_tape_vwap_calculation(self) -> None:
        agg = TradeTapeAgg(duration_seconds=60)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        agg.add_trade(Decimal("100"), Decimal("10"), "buy", base_time)
        agg.add_trade(Decimal("200"), Decimal("5"), "sell", base_time + timedelta(seconds=10))
        vwap = agg.get_vwap()
        expected = (Decimal("100") * Decimal("10") + Decimal("200") * Decimal("5")) / Decimal("15")
        assert abs(vwap - expected) < Decimal("0.01")

    def test_trade_tape_aggressor_ratio(self) -> None:
        agg = TradeTapeAgg(duration_seconds=60)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        agg.add_trade(Decimal("100"), Decimal("1"), "buy", base_time)
        agg.add_trade(Decimal("100"), Decimal("1"), "buy", base_time + timedelta(seconds=10))
        agg.add_trade(Decimal("100"), Decimal("1"), "sell", base_time + timedelta(seconds=20))
        assert agg.get_aggressor_ratio() == 2 / 3

    def test_to_product_spot_market(self) -> None:
        payload = {
            "product_id": "BTC-USD",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "base_min_size": "0.001",
            "base_increment": "0.00001",
            "quote_increment": "0.01",
            "min_notional": "10",
        }
        product = to_product(payload)
        assert product.symbol == "BTC-USD"
        assert product.market_type == MarketType.SPOT
        assert product.contract_size is None
        assert product.funding_rate is None
        assert product.next_funding_time is None
        assert product.leverage_max is None

    def test_to_product_perpetual_full(self) -> None:
        payload = {
            "product_id": "BTC-PERP",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "contract_type": "perpetual",
            "base_min_size": "0.001",
            "base_increment": "0.00001",
            "quote_increment": "0.01",
            "min_notional": "10",
            "max_leverage": 20,
            "contract_size": "1",
            "funding_rate": "0.0001",
            "next_funding_time": "2024-01-15T16:00:00Z",
        }
        product = to_product(payload)
        assert product.symbol == "BTC-PERP"
        assert product.market_type == MarketType.PERPETUAL
        assert product.contract_size == Decimal("1")
        assert product.funding_rate == Decimal("0.0001")
        assert product.next_funding_time == datetime(2024, 1, 15, 16, 0, 0, tzinfo=UTC)
        assert product.leverage_max == 20

    def test_to_product_perpetual_partial(self) -> None:
        payload = {
            "product_id": "ETH-PERP",
            "base_currency": "ETH",
            "quote_currency": "USD",
            "contract_type": "perpetual",
            "base_min_size": "0.01",
            "base_increment": "0.001",
            "quote_increment": "0.1",
        }
        product = to_product(payload)
        assert product.symbol == "ETH-PERP"
        assert product.market_type == MarketType.PERPETUAL
        assert product.contract_size is None
        assert product.funding_rate is None
        assert product.next_funding_time is None
        assert product.leverage_max is None

    def test_to_product_future_market(self) -> None:
        payload = {
            "product_id": "BTC-USD-240331",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "contract_type": "future",
            "base_min_size": "0.001",
            "base_increment": "0.00001",
            "quote_increment": "0.01",
            "expiry": "2024-03-31T08:00:00Z",
            "contract_size": "1",
        }
        product = to_product(payload)
        assert product.symbol == "BTC-USD-240331"
        assert product.market_type == MarketType.FUTURES
        assert product.expiry == datetime(2024, 3, 31, 8, 0, 0, tzinfo=UTC)
        assert product.contract_size == Decimal("1")

    def test_to_product_invalid_funding_time(self) -> None:
        payload = {
            "product_id": "BTC-PERP",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "contract_type": "perpetual",
            "base_min_size": "0.001",
            "base_increment": "0.00001",
            "quote_increment": "0.01",
            "next_funding_time": "invalid-date",
        }
        product = to_product(payload)
        assert product.next_funding_time is None


class TestCoinbaseMarketDataStaleness:
    def test_staleness_detection_fresh_vs_stale_toggles(self) -> None:
        config = APIConfig(
            api_key="test",
            api_secret="test",
            passphrase="test",
            base_url="https://api.sandbox.coinbase.com",
            sandbox=True,
            enable_derivatives=True,
            api_mode="advanced",
            auth_type="JWT",
        )
        adapter = MinimalCoinbaseBrokerage(config)
        symbol = "BTC-PERP"
        assert adapter.is_stale(symbol, threshold_seconds=10) is True
        assert adapter.is_stale(symbol, threshold_seconds=1) is True
        adapter.start_market_data([symbol])
        assert adapter.is_stale(symbol, threshold_seconds=10) is False
        assert adapter.is_stale(symbol, threshold_seconds=1) is False

    def test_staleness_behavior_matches_validator(self) -> None:
        config = APIConfig(
            api_key="test",
            api_secret="test",
            passphrase="test",
            base_url="https://api.sandbox.coinbase.com",
            sandbox=True,
            enable_derivatives=True,
            api_mode="advanced",
            auth_type="JWT",
        )
        adapter = MinimalCoinbaseBrokerage(config)
        symbol = "ETH-PERP"
        assert adapter.is_stale(symbol) is True
        adapter.start_market_data([symbol])
        assert adapter.is_stale(symbol) is False
        assert adapter.is_stale(symbol, threshold_seconds=1) is False
