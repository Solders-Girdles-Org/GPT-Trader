"""Recorded bars that naturally exercise the shipped moving-average benchmark."""

from datetime import UTC, datetime, timedelta

import pytest

from gpt_trader.features.experiment.inputs import parse_input


@pytest.fixture
def experiment_payload():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = [100] * 50 + [98, 98, 98, 102, 110, 110, 111, 130, 130, 130]
    candles = []
    for index, close in enumerate(closes):
        opening = close if index != 57 else 111
        candles.append(
            {
                "ts": (start + timedelta(hours=index)).isoformat(),
                "open": str(opening),
                "high": str(max(opening, close) + 1),
                "low": str(min(opening, close) - 1),
                "close": str(close),
                "volume": "100",
            }
        )
    return {
        "schema_version": 1,
        "source": "Synthetic crossover mechanics fixture; no performance evidence",
        "symbol": "BTC-USD",
        "recorded_at": (start + timedelta(hours=len(candles))).isoformat(),
        "candles": candles,
    }


@pytest.fixture
def experiment_input(experiment_payload):
    return parse_input(experiment_payload)
