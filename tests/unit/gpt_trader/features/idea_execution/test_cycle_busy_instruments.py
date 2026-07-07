"""The busy-instrument map shared by re-proposal gating and snapshot top-up.

``busy_instruments`` gates near-duplicate proposals inside the turn and keeps
snapshot acquisition fetching candles for unresolved instruments after the
configured universe drops them (issue #1215). The proposer-leg skip behavior it
feeds is pinned in test_cycle.py; this pins the map itself across one idea's
life. Shared builders live in conftest.py.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.unit.gpt_trader.features.idea_execution.conftest import (
    build_cycle_idea,
    crossover_series,
    make_cycle_runner,
    snapshot,
    snapshot_provider,
)

from gpt_trader.features.idea_execution import busy_instruments
from gpt_trader.features.trade_ideas import TradeIdeaService, TradeIdeaState


def test_busy_map_tracks_the_idea_lifecycle_and_frees_on_attribution(
    cycle_service: TradeIdeaService, tmp_path: Path
) -> None:
    assert busy_instruments(cycle_service) == {}

    decision_id = "trade-20260703-cycle-busy-map"
    cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
    blocker = busy_instruments(cycle_service)["btc-usd"]
    assert blocker.instrument == "BTC-USD"
    assert blocker.decision_id == decision_id
    assert blocker.reason == "instrument already has an open idea"

    cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")
    make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
        snapshot_provider(snapshot(crossover_series("BTC-USD")))
    )
    assert cycle_service.get(decision_id).state is TradeIdeaState.FILLED
    blocker = busy_instruments(cycle_service)["btc-usd"]
    assert blocker.reason == "instrument has a filled idea awaiting closeout"

    cycle_service.record_closeout_attribution(
        decision_id,
        actor_id="test-operator",
        resolution="thesis_target",
        realized_profit_loss_amount=Decimal("10"),
        realized_profit_loss_percent=Decimal("0.04"),
        evidence=("test closeout",),
    )
    assert busy_instruments(cycle_service) == {}
