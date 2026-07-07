"""Golden-record and instrument-accessor tests for TradeIdea.

The golden payload and its record hash were captured from a TradeIdea
serialized BEFORE the structured Instrument taxonomy existed (#1230). Idea
records are content-hashed and audit events chain on those hashes, so this
test failing means the persisted schema changed and the audit trail would be
corrupted. Do not update the pinned hash to make it pass — fix the
serialization instead.
"""

from __future__ import annotations

import copy

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.core.instruments import (
    AssetClass,
    Instrument,
    InstrumentParseError,
    ProductType,
)
from gpt_trader.features.trade_ideas import TradeIdea

# Captured on main at c681c6ec, before core/instruments.py existed.
GOLDEN_RECORD_HASH = "350ec4901f3f6c8ccc409628c656384882c307af0d19d95159215076b841d58d"

GOLDEN_PAYLOAD: dict[str, object] = {
    "decision_id": "trade-20260612-001",
    "autonomy_mode": "human_approved_execution",
    "thesis": "BTC reclaiming the 50-day average with rising spot volume",
    "instrument": "BTC-USD",
    "product_type": "spot",
    "direction": "long",
    "entry_zone": {"lower": "60000", "upper": "61500", "trigger": ""},
    "invalidation": "Daily close below 58000",
    "target_exit": "Take profit at 67000 or exit after 10 trading days",
    "max_loss": {
        "amount": "250",
        "percent_of_account": "1.5",
        "assumptions": ["Fill at zone midpoint", "No slippage beyond 10 bps"],
    },
    "sizing_recommendation": {
        "quantity": "0.1",
        "notional": "6075",
        "rationale": "Half-Kelly on backtested edge",
    },
    "time_horizon": {
        "expected_hold": "3-10 days",
        "expires_at": "2026-06-19T16:00:00+00:00",
    },
    "data_used": ["coinbase:candles:BTC-USD:1d:2026-06-11"],
    "confidence": {
        "label": "medium",
        "rationale": "Volume confirmation present, macro calendar risk this week",
    },
    "failure_mode": "False breakout into a macro-driven selloff",
    "do_not_trade_if": ["FOMC announcement within 24 hours"],
    "broker_ticket": {"venue": "none", "status": "not_created"},
}


def test_golden_record_round_trips_unchanged() -> None:
    idea = TradeIdea.from_dict(copy.deepcopy(GOLDEN_PAYLOAD))

    assert idea.to_dict() == GOLDEN_PAYLOAD
    assert isinstance(idea.to_dict()["instrument"], str)


def test_golden_record_hash_is_pinned() -> None:
    idea = TradeIdea.from_dict(copy.deepcopy(GOLDEN_PAYLOAD))

    assert idea.record_hash() == GOLDEN_RECORD_HASH


def test_instrument_info_derives_structure_from_the_string() -> None:
    idea = build_trade_idea()

    assert idea.instrument_info == Instrument(
        symbol="BTC-USD",
        asset_class=AssetClass.CRYPTO,
        product_type=ProductType.SPOT,
    )


def test_instrument_info_classifies_equity_tickers() -> None:
    idea = build_trade_idea(instrument="AAPL")

    assert idea.instrument_info.asset_class is AssetClass.EQUITY


def test_instrument_info_is_loud_on_ambiguous_strings() -> None:
    idea = build_trade_idea(instrument="btc/usd")

    with pytest.raises(InstrumentParseError, match="ambiguous instrument string"):
        _ = idea.instrument_info


def test_instrument_info_is_derived_and_never_persisted() -> None:
    payload = build_trade_idea().to_dict()

    assert "instrument_info" not in payload
    assert "asset_class" not in payload
