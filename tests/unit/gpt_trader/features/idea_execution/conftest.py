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
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

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
    ExitPlan,
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


# --- shared exit-monitor builders (test_exit_monitor*.py) ---

EXIT_CLOCK = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
EXIT_QUANTITY = Decimal("0.1")


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    """Frozen-clock service for the exit-monitor test modules."""
    built = TradeIdeaService(tmp_path / "trade_ideas", now_factory=lambda: EXIT_CLOCK)
    attest_account_equity(built)
    return built


def fill_exit_idea(
    service: TradeIdeaService,
    *,
    decision_id: str = "trade-20260612-001",
    instrument: str = "BTC-USD",
    fill_evidence: tuple[str, ...] = (),
) -> None:
    """Propose/approve/submit/fill one long idea (zone 100-102, stop 95, target 113)."""
    idea = build_trade_idea(
        decision_id=decision_id,
        instrument=instrument,
        entry_zone=EntryZone(lower=Decimal("100"), upper=Decimal("102")),
        invalidation="Close below 95",
        target_exit="Take profit at 113 or exit at expiry",
        exit_plan=ExitPlan(stop=Decimal("95"), target=Decimal("113")),
        sizing_recommendation=SizingRecommendation(
            quantity=EXIT_QUANTITY, notional=Decimal("10.1"), rationale="test"
        ),
        time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=EXIT_CLOCK + timedelta(hours=4)),
    )
    service.propose(idea, actor_id="proposer")
    service.approve(decision_id, actor_id="rj", reason="verified")
    service.record_submission(decision_id, actor_id="executor", venue="coinbase")
    service.record_fill(
        decision_id,
        actor_id="coinbase",
        venue="coinbase",
        evidence=fill_evidence,
    )


def exit_candle(offset_hours: int, *, high: str, low: str, close: str) -> Candle:
    price = Decimal(close)
    return Candle(
        ts=EXIT_CLOCK + timedelta(hours=offset_hours),
        open=price,
        high=Decimal(high),
        low=Decimal(low),
        close=price,
        volume=Decimal("1000"),
    )


def exit_snapshot(*candles: Candle, symbol: str = "BTC-USD") -> MarketSnapshot:
    # as_of sits after the recorded candles: the monitor runs on a later turn's
    # snapshot whose bars span the position's post-entry history.
    return MarketSnapshot(
        as_of=EXIT_CLOCK + timedelta(hours=3),
        source="test:fixture",
        series=(SymbolSeries(symbol=symbol, granularity="ONE_HOUR", candles=candles),),
    )
