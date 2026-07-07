"""Tests for ticker-price validation on the live-mark path (#1123).

Runs against the real-flow engine (production validator/submitter/state
stack); behavior is steered only at the broker boundary. A ticker price that
is malformed, non-finite, or non-positive must never be recorded as a live
mark: no tick persisted, no mark-staleness seed, no strategy decision, and
the connection is reported DISCONNECTED.
"""

from __future__ import annotations

import pytest


def _recorded_marks(engine) -> list:
    return list(engine.price_history.get("BTC-USD", []))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_price",
    [
        "not-a-number",
        "NaN",
        "Infinity",
        "-Infinity",
        "0",
        "-5",
        "",
        None,
    ],
)
async def test_invalid_ticker_price_never_recorded_as_mark(real_flow_engine, raw_price) -> None:
    engine = real_flow_engine
    engine.context.broker.get_ticker.return_value = {"price": raw_price}

    await engine._cycle()

    engine.strategy.decide.assert_not_called()
    assert engine._connection_status == "DISCONNECTED"
    assert "BTC-USD" not in engine.context.risk_manager.last_mark_update
    assert not _recorded_marks(engine)


@pytest.mark.asyncio
async def test_valid_ticker_price_recorded_as_mark(real_flow_engine) -> None:
    engine = real_flow_engine
    engine.context.broker.get_ticker.return_value = {"price": "50000.5"}

    await engine._cycle()

    engine.strategy.decide.assert_called_once()
    assert engine._connection_status == "CONNECTED"
    assert "BTC-USD" in engine.context.risk_manager.last_mark_update
    marks = _recorded_marks(engine)
    assert marks
    assert str(marks[-1]) == "50000.5"
