"""Closeout auto-attribution leg of the paper cycle (issue #1214).

The turn attributes every expired-unexecuted idea so the closeout trail
self-heals each turn. attribution_coverage is a hard Stage 1->2 promotion gate;
an expired idea never opened a position (SUBMITTED cannot expire), so an EXPIRY
closeout with realized P&L unavailable keeps coverage honest at 100% without a
manual `ideas closeout record` per expiry. Shared builders live in conftest.py.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from tests.unit.gpt_trader.features.idea_execution.conftest import (
    CYCLE_NOW,
    build_cycle_idea,
    flat_series,
    make_cycle_runner,
    manifest_rows,
    snapshot,
    snapshot_provider,
)

from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas import (
    ActorType,
    CloseoutResolution,
    SymbolSeries,
    TimeHorizon,
    TradeIdeaService,
)


def test_swept_idea_is_attributed_in_the_same_turn(
    cycle_service: TradeIdeaService, tmp_path: Path
) -> None:
    decision_id = "trade-20260703-cycle-005"
    stale = replace(
        build_cycle_idea(decision_id),
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=CYCLE_NOW - timedelta(hours=1),
        ),
    )
    cycle_service.propose(stale, actor_id="test-proposer")

    result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
        snapshot_provider(snapshot(flat_series("BTC-USD")))
    )

    assert result.attributed_decision_ids == (decision_id,)
    closeout = cycle_service.get_closeout_attribution(decision_id)
    assert closeout is not None
    assert closeout.resolution is CloseoutResolution.EXPIRY
    assert closeout.actor_type == ActorType.SYSTEM.value
    assert closeout.realized_profit_loss_amount is None
    assert closeout.realized_profit_loss_unavailable_reason


def test_preexisting_expired_idea_is_backfilled(
    cycle_service: TradeIdeaService, tmp_path: Path
) -> None:
    decision_id = "trade-20260703-cycle-006"
    cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
    cycle_service.expire(decision_id)
    assert cycle_service.get_closeout_attribution(decision_id) is None

    # A later turn that expires nothing new still heals the stuck idea.
    result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
        snapshot_provider(snapshot(flat_series("BTC-USD")))
    )

    assert result.expired_decision_ids == ()
    assert result.attributed_decision_ids == (decision_id,)
    assert cycle_service.get_closeout_attribution(decision_id) is not None


def test_legacy_fill_is_repaired_from_manifest_execution_evidence(
    cycle_service: TradeIdeaService, tmp_path: Path
) -> None:
    """The cycle supplies its own manifest fill facts as fallback evidence (#1212).

    Fills recorded before fill-evidence persistence existed carry no price on
    the FILLED audit event; the executed price still lives on the manifest's
    execution row. The exit monitor must resolve such a position from that
    durable evidence — even when the fill landed outside the planned entry
    zone, which the old proposal-replay path could never resolve.
    """
    decision_id = "trade-20260703-cycle-legacy"
    cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
    cycle_service.approve(decision_id, actor_id="rj", reason="verified")
    cycle_service.record_submission(
        decision_id, actor_id="executor", venue="paper", external_order_id="MOCK_000001"
    )
    # Legacy fill: no evidence on the audit event, price 62000 outside zone
    # 60000-61500 so the zone midpoint is never revisited by the candles below.
    cycle_service.record_fill(
        decision_id, actor_id="paper-cycle", venue="paper", external_order_id="MOCK_000001"
    )
    manifest_path = tmp_path / "cycle" / "manifest.jsonl"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "cycle-20260703T120000Z-legacy",
                "execution": {
                    "enabled": True,
                    "executed": [
                        {
                            "decision_id": decision_id,
                            "client_order_id": decision_id,
                            "order_id": "MOCK_000001",
                            "symbol": "BTC-USD",
                            "side": "buy",
                            "quantity": "0.1",
                            "fill_price": "62000",
                            "final_state": "filled",
                        }
                    ],
                    "skipped": [],
                },
            },
            sort_keys=True,
        )
        + "\n"
    )

    # A later turn: post-fill candles ride from 62000 to the 67000 target
    # without ever touching the planned zone midpoint 60750.
    later = CYCLE_NOW + timedelta(hours=6)
    candles = tuple(
        Candle(
            ts=CYCLE_NOW + timedelta(hours=index + 1),
            open=Decimal("62000"),
            high=Decimal("62500") if index < 3 else Decimal("67100"),
            low=Decimal("61900"),
            close=Decimal("62200"),
            volume=Decimal("10"),
        )
        for index in range(4)
    )
    series = SymbolSeries(symbol="BTC-USD", granularity="ONE_HOUR", candles=candles)

    result = make_cycle_runner(cycle_service, tmp_path, proposers=[], now=later).run(
        snapshot_provider(snapshot(series, as_of=later))
    )

    assert result.resolved_decision_ids == (decision_id,)
    closeout = cycle_service.get_closeout_attribution(decision_id)
    assert closeout is not None
    assert closeout.resolution is CloseoutResolution.THESIS_TARGET
    # manifest fill 62000 -> target 67000 on qty 0.1: +500
    assert closeout.realized_profit_loss_amount == Decimal("500.0")
    assert "entry_price_source=cycle_manifest" in closeout.evidence


def test_manifest_fallback_survives_corruption_and_keeps_first_fill(
    cycle_service: TradeIdeaService, tmp_path: Path
) -> None:
    """Corrupt manifest lines are counted on the turn's row; first fill wins."""
    decision_id = "trade-20260703-cycle-legacy"
    cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
    cycle_service.approve(decision_id, actor_id="rj", reason="verified")
    cycle_service.record_submission(
        decision_id, actor_id="executor", venue="paper", external_order_id="MOCK_000001"
    )
    cycle_service.record_fill(
        decision_id, actor_id="paper-cycle", venue="paper", external_order_id="MOCK_000001"
    )
    manifest_path = tmp_path / "cycle" / "manifest.jsonl"
    manifest_path.parent.mkdir(parents=True)

    def _row(fill_price: str) -> str:
        return json.dumps(
            {
                "run_id": f"cycle-legacy-{fill_price}",
                "execution": {
                    "enabled": True,
                    "executed": [
                        {
                            "decision_id": decision_id,
                            "order_id": "MOCK_000001",
                            "symbol": "BTC-USD",
                            "side": "buy",
                            "quantity": "0.1",
                            "fill_price": fill_price,
                            "final_state": "filled",
                        }
                    ],
                    "skipped": [],
                },
            },
            sort_keys=True,
        )

    manifest_path.write_text(
        '{"run_id": "cycle-corrupt", "execution":'  # truncated write from a killed turn
        + "\n"
        + _row("62000")
        + "\n"
        + _row("64000")  # later duplicate must not override the original fill
        + "\n"
    )

    later = CYCLE_NOW + timedelta(hours=6)
    candles = tuple(
        Candle(
            ts=CYCLE_NOW + timedelta(hours=index + 1),
            open=Decimal("62000"),
            high=Decimal("62500") if index < 3 else Decimal("67100"),
            low=Decimal("61900"),
            close=Decimal("62200"),
            volume=Decimal("10"),
        )
        for index in range(4)
    )
    series = SymbolSeries(symbol="BTC-USD", granularity="ONE_HOUR", candles=candles)

    make_cycle_runner(cycle_service, tmp_path, proposers=[], now=later).run(
        snapshot_provider(snapshot(series, as_of=later))
    )

    closeout = cycle_service.get_closeout_attribution(decision_id)
    assert closeout is not None
    # first recorded fill (62000) wins: 67000 target on qty 0.1 -> +500
    assert closeout.realized_profit_loss_amount == Decimal("500.0")
    # manifest_rows() would choke on the deliberately corrupt line; the turn's
    # own row is the last (valid) line.
    turn_row = json.loads(manifest_path.read_text().splitlines()[-1])
    assert turn_row["legacy_fill_manifest_unreadable_lines"] == 1


def test_unresolved_exit_monitor_entries_land_on_the_manifest_row(
    cycle_service: TradeIdeaService, tmp_path: Path
) -> None:
    """An expired fill the monitor cannot close explains itself in the evidence."""
    decision_id = "trade-20260703-cycle-stuck"
    stuck = replace(
        build_cycle_idea(decision_id),
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=CYCLE_NOW + timedelta(hours=1),
        ),
    )
    cycle_service.propose(stuck, actor_id="test-proposer")
    cycle_service.approve(decision_id, actor_id="rj", reason="verified")
    cycle_service.record_submission(
        decision_id, actor_id="executor", venue="paper", external_order_id="MOCK_000001"
    )
    cycle_service.record_fill(
        decision_id, actor_id="paper-cycle", venue="paper", external_order_id="MOCK_000001"
    )

    # A turn after expiry whose snapshot has no candles for the instrument.
    later = CYCLE_NOW + timedelta(hours=6)
    result = make_cycle_runner(cycle_service, tmp_path, proposers=[], now=later).run(
        snapshot_provider(snapshot(flat_series("ETH-USD"), as_of=later))
    )

    assert result.resolved_decision_ids == ()
    (entry,) = result.exit_monitor_unresolved
    assert entry["decision_id"] == decision_id
    assert "no candles" in entry["reason"]
    rows = manifest_rows(tmp_path)
    assert rows[-1]["exit_monitor_unresolved"][0]["decision_id"] == decision_id
