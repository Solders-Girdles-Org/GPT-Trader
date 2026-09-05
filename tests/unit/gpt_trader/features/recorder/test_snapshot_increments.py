"""Public product metadata reaches real strategy proposal callers without trading."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.cli.commands.ideas import _snapshot_strategy_proposer
from gpt_trader.cli.commands.ideas_input import _load_market_snapshot, _market_snapshot_from_payload
from gpt_trader.core.math.quantization import quantize_price_nearest
from gpt_trader.errors import ValidationError
from gpt_trader.features.recorder.snapshot_builder import MarketSnapshotBuildRequest
from gpt_trader.features.recorder.snapshot_source import build_coinbase_market_snapshot
from gpt_trader.features.trade_ideas import market_snapshot_to_payload

FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "fixtures/market_snapshots/dot_coarse_precision_20260902.json"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata", ["valid", "missing", "wrong-symbol", "nan", "zero", "negative"]
)
async def test_recorded_coarse_precision_failure_uses_product_metadata(monkeypatch, metadata):
    recorded = _load_market_snapshot(FIXTURE)
    proposer = _snapshot_strategy_proposer("mean-reversion", price_precision=Decimal("0.01"))
    with pytest.raises(ValidationError, match="too coarse"):
        proposer.propose(recorded)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["auth"] is None

        def get_market_product(self, symbol):
            calls.append(symbol)
            if metadata == "missing":
                raise ConnectionError("metadata unavailable")
            # API contract fixture, not a claim about the historical venue rule.
            return {
                "product_id": "BTC-USD" if metadata == "wrong-symbol" else symbol,
                "quote_increment": "0.01",
                "price_increment": {"nan": "NaN", "zero": "0", "negative": "-1"}.get(
                    metadata, "0.0001"
                ),
            }

        def close(self):
            calls.append("closed")

    class Fetcher:
        def __init__(self, **kwargs):
            pass

        async def fetch_candles(self, *args, **kwargs):
            return recorded.series[0].candles

    monkeypatch.setattr("gpt_trader.features.brokerages.coinbase.client.CoinbaseClient", Client)
    monkeypatch.setattr(
        "gpt_trader.features.brokerages.coinbase.historical_candles.CoinbaseHistoricalFetcher",
        Fetcher,
    )
    captured = await build_coinbase_market_snapshot(
        MarketSnapshotBuildRequest(
            symbols=("DOT-USD",), granularity="ONE_HOUR", lookback=30, as_of=recorded.as_of
        )
    )
    assert calls == ["DOT-USD", "closed"]
    assert captured.series[0].candles == recorded.series[0].candles
    restored = _market_snapshot_from_payload(market_snapshot_to_payload(captured))
    assert restored == captured
    if metadata != "valid":
        with pytest.raises(ValidationError, match="increment unavailable"):
            proposer.propose(restored)
        return
    (idea,) = proposer.propose(restored)
    assert idea.exit_plan is not None
    assert (
        idea.exit_plan.stop < idea.entry_zone.lower < idea.entry_zone.upper < idea.exit_plan.target
    )
    assert captured.series[0].price_increment == Decimal(".0001")
    assert "observed_at=" in captured.series[0].price_increment_source
    # Structured levels remain executable even if presentation prose changes.
    from gpt_trader.features.trade_ideas import exit_plan_scoring_levels

    levels = exit_plan_scoring_levels(
        replace(idea, invalidation="unparseable", target_exit="unparseable")
    )
    assert (levels.stop, levels.target) == (idea.exit_plan.stop, idea.exit_plan.target)


def test_price_is_a_multiple_of_non_decimal_tick():
    assert quantize_price_nearest(Decimal("1.23"), Decimal(".05")) == Decimal("1.25")
