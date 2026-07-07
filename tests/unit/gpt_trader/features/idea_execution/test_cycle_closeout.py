"""Closeout auto-attribution leg of the paper cycle (issue #1214).

The turn attributes every expired-unexecuted idea so the closeout trail
self-heals each turn. attribution_coverage is a hard Stage 1->2 promotion gate;
an expired idea never opened a position (SUBMITTED cannot expire), so an EXPIRY
closeout with realized P&L unavailable keeps coverage honest at 100% without a
manual `ideas closeout record` per expiry. Shared builders live in conftest.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from tests.unit.gpt_trader.features.idea_execution.conftest import (
    CYCLE_NOW,
    build_cycle_idea,
    flat_series,
    make_cycle_runner,
    snapshot,
    snapshot_provider,
)

from gpt_trader.features.trade_ideas import (
    ActorType,
    CloseoutResolution,
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
