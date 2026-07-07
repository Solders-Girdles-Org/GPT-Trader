"""Shared fixtures and builders for the paper cycle test modules.

test_cycle.py pins turn behavior (propose/execute/expiry legs);
test_cycle_evidence.py pins the manifest, artifact, and locking contract.
Both drive the runner through the same synthetic snapshots built here.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader.core import Candle
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import PaperCycleRunner
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AutonomyMode,
    BaselineProposer,
    Confidence,
    ConfidenceLabel,
    EntryZone,
    MarketSnapshot,
    MaxLoss,
    ProductType,
    SizingRecommendation,
    SymbolSeries,
    TimeHorizon,
    TradeDirection,
    TradeIdea,
    TradeIdeaService,
)

CYCLE_NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def crossover_series(symbol: str, *, as_of: datetime = CYCLE_NOW) -> SymbolSeries:
    """Sixty hourly candles whose 10/50 MA golden cross lands in the last 3 bars."""
    closes = [Decimal("100")] * 57 + [Decimal("120"), Decimal("125"), Decimal("130")]
    volumes = [Decimal("10")] * 59 + [Decimal("100")]
    start = as_of - timedelta(hours=len(closes))
    candles = tuple(
        Candle(
            ts=start + timedelta(hours=index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
        )
        for index, (close, volume) in enumerate(zip(closes, volumes, strict=True))
    )
    return SymbolSeries(symbol=symbol, granularity="ONE_HOUR", candles=candles)


def flat_series(symbol: str) -> SymbolSeries:
    """Sixty flat candles: never triggers the baseline proposer."""
    start = CYCLE_NOW - timedelta(hours=60)
    candles = tuple(
        Candle(
            ts=start + timedelta(hours=index),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("10"),
        )
        for index in range(60)
    )
    return SymbolSeries(symbol=symbol, granularity="ONE_HOUR", candles=candles)


def snapshot(*series: SymbolSeries, as_of: datetime = CYCLE_NOW) -> MarketSnapshot:
    return MarketSnapshot(as_of=as_of, source="test:fixture", series=tuple(series))


def snapshot_provider(market_snapshot: MarketSnapshot):
    return lambda: (market_snapshot, "test:fixture:reference")


def build_cycle_idea(decision_id: str, *, instrument: str = "BTC-USD") -> TradeIdea:
    return TradeIdea(
        decision_id=decision_id,
        autonomy_mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
        thesis="Cycle test: eligible fixture record",
        instrument=instrument,
        product_type=ProductType.SPOT,
        direction=TradeDirection.LONG,
        entry_zone=EntryZone(lower=Decimal("60000"), upper=Decimal("61500")),
        invalidation="Daily close below 58000",
        target_exit="Take profit at 67000",
        max_loss=MaxLoss(
            amount=Decimal("250"),
            percent_of_account=Decimal("1.5"),
            assumptions=("Fill at zone midpoint",),
        ),
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=Decimal("6075"),
            rationale="Fixture sizing",
        ),
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=CYCLE_NOW + timedelta(days=7),
        ),
        data_used=("test:fixture:no-market-data",),
        confidence=Confidence(label=ConfidenceLabel.MEDIUM, rationale="fixture"),
        failure_mode="Not applicable in tests",
        do_not_trade_if=("fixture record",),
    )


@pytest.fixture
def cycle_service(tmp_path: Path) -> TradeIdeaService:
    trade_idea_service = TradeIdeaService(tmp_path / "ideas", now_factory=lambda: CYCLE_NOW)
    trade_idea_service.update_budget(
        replace(
            DEFAULT_RISK_BUDGET,
            version=2,
            account_equity=Decimal("25000"),
            reason="test: attest scratch equity",
        ),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    return trade_idea_service


def make_cycle_runner(
    service: TradeIdeaService,
    tmp_path: Path,
    *,
    proposers: list | None = None,
    execute_approved: bool = True,
    now: datetime = CYCLE_NOW,
) -> PaperCycleRunner:
    return PaperCycleRunner(
        service,
        cycle_root=tmp_path / "cycle",
        proposers=proposers if proposers is not None else [BaselineProposer()],
        broker=DeterministicBroker(),
        execute_approved=execute_approved,
        now_factory=lambda: now,
    )


def manifest_rows(tmp_path: Path) -> list[dict[str, Any]]:
    manifest_path = tmp_path / "cycle" / "manifest.jsonl"
    if not manifest_path.exists():
        return []
    return [json.loads(line) for line in manifest_path.read_text().splitlines() if line]
