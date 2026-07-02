"""Turn-behavior tests for the Stage-1 paper cycle runner (issue #1150).

These pin the propose, execute-approved, and expiry legs of one unattended
turn: the open-instrument and awaiting-closeout dedup filters, snapshot-priced
execution of pre-turn-approved ideas only, and the human-approval seam between
turns. The manifest/artifact/locking contract is pinned in
test_cycle_evidence.py; shared builders live in conftest.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from tests.unit.gpt_trader.features.idea_execution.conftest import (
    CYCLE_NOW,
    build_cycle_idea,
    crossover_series,
    flat_series,
    make_cycle_runner,
    snapshot,
    snapshot_provider,
)

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import PaperCycleRunner
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    MarketSnapshot,
    TimeHorizon,
    TradeIdeaService,
    TradeIdeaState,
)


class TestProposeLeg:
    def test_crossover_snapshot_proposes_idea(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        result = make_cycle_runner(cycle_service, tmp_path).run(
            snapshot_provider(snapshot(crossover_series("BTC-USD")))
        )

        (proposer_turn,) = result.proposer_turns
        assert proposer_turn.proposal_count == 1
        (decision_id,) = proposer_turn.proposed_decision_ids
        view = cycle_service.get(decision_id)
        assert view.state is TradeIdeaState.PROPOSED
        assert view.events[0].actor_type is ActorType.AI
        assert view.events[0].actor_id == proposer_turn.proposer_id

    def test_flat_snapshot_is_honest_noop(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        result = make_cycle_runner(cycle_service, tmp_path).run(
            snapshot_provider(snapshot(flat_series("BTC-USD")))
        )
        (proposer_turn,) = result.proposer_turns
        assert proposer_turn.proposal_count == 0

    def test_open_instrument_is_not_reproposed(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        runner = make_cycle_runner(cycle_service, tmp_path)
        provider = snapshot_provider(snapshot(crossover_series("BTC-USD")))
        first = runner.run(provider)
        assert first.proposer_turns[0].proposal_count == 1

        second = runner.run(provider)
        (proposer_turn,) = second.proposer_turns
        assert proposer_turn.proposal_count == 0
        (skip,) = proposer_turn.skipped_open_instruments
        assert skip["instrument"] == "BTC-USD"
        assert skip["existing_decision_id"] == first.proposer_turns[0].proposed_decision_ids[0]

    def test_rerun_over_resolved_idea_skips_duplicate_id_idempotently(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        # The deterministic proposer emits the same decision_id for the same
        # snapshot; a rerun after the prior idea resolved (cancelled here)
        # must skip instead of failing the turn on a duplicate id.
        runner = make_cycle_runner(cycle_service, tmp_path)
        provider = snapshot_provider(snapshot(crossover_series("BTC-USD")))
        first = runner.run(provider)
        (decision_id,) = first.proposer_turns[0].proposed_decision_ids
        cycle_service.reject(decision_id, actor_id="test-operator", reason="not this one")

        second = runner.run(provider)
        (proposer_turn,) = second.proposer_turns
        assert proposer_turn.proposal_count == 0
        (skip,) = proposer_turn.skipped_open_instruments
        assert skip["reason"] == "decision id already recorded (idempotent rerun)"
        assert skip["existing_decision_id"] == decision_id

    def test_filled_idea_awaiting_closeout_blocks_reproposal(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        # The same crossover stays signal-worthy for several bars; until the
        # filled trade's outcome is attributed, the next turns must not
        # pyramid a second position onto the unresolved one.
        runner = make_cycle_runner(cycle_service, tmp_path)
        first = runner.run(snapshot_provider(snapshot(crossover_series("BTC-USD"))))
        (decision_id,) = first.proposer_turns[0].proposed_decision_ids
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")

        # Turn 2: the proposer leg still sees the APPROVED (open) idea; the
        # execution leg then fills it.
        later = CYCLE_NOW + timedelta(hours=1)
        second = runner.run(
            snapshot_provider(snapshot(crossover_series("BTC-USD", as_of=later), as_of=later))
        )
        assert second.execution.executed[0]["decision_id"] == decision_id

        # Turn 3: the trade is FILLED but unattributed — still busy.
        third_time = CYCLE_NOW + timedelta(hours=2)
        third = runner.run(
            snapshot_provider(
                snapshot(crossover_series("BTC-USD", as_of=third_time), as_of=third_time)
            )
        )
        (skip,) = third.proposer_turns[0].skipped_open_instruments
        assert skip["reason"] == "instrument has a filled idea awaiting closeout"
        assert skip["existing_decision_id"] == decision_id

        cycle_service.record_closeout_attribution(
            decision_id,
            actor_id="test-operator",
            resolution="thesis_target",
            realized_profit_loss_amount=Decimal("10"),
            realized_profit_loss_percent=Decimal("0.04"),
            evidence=("test closeout",),
        )
        fourth_time = CYCLE_NOW + timedelta(hours=3)
        fourth = runner.run(
            snapshot_provider(
                snapshot(crossover_series("BTC-USD", as_of=fourth_time), as_of=fourth_time)
            )
        )
        assert fourth.proposer_turns[0].proposal_count == 1


class TestExecuteApprovedLeg:
    def test_executes_approved_idea_at_snapshot_mark(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-001"
        cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")

        result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
            snapshot_provider(snapshot(crossover_series("BTC-USD")))
        )

        (executed,) = result.execution.executed
        assert executed["decision_id"] == decision_id
        assert executed["client_order_id"] == decision_id
        # Priced from the turn's snapshot: the fixture's last close is 130.
        assert executed["fill_price"] == "130"
        assert cycle_service.get(decision_id).state is TradeIdeaState.FILLED

    def test_skips_approved_idea_without_fresh_mark(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-002"
        cycle_service.propose(
            build_cycle_idea(decision_id, instrument="ETH-USD"), actor_id="test-proposer"
        )
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")

        result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
            snapshot_provider(snapshot(crossover_series("BTC-USD")))
        )

        assert result.execution.executed == ()
        (skip,) = result.execution.skipped
        assert skip["decision_id"] == decision_id
        assert "no fresh mark" in skip["reason"]
        assert cycle_service.get(decision_id).state is TradeIdeaState.APPROVED

    def test_execution_leg_can_be_disabled(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-003"
        cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")

        result = make_cycle_runner(
            cycle_service, tmp_path, proposers=[], execute_approved=False
        ).run(snapshot_provider(snapshot(crossover_series("BTC-USD"))))

        assert result.execution.enabled is False
        assert cycle_service.get(decision_id).state is TradeIdeaState.APPROVED

    def test_proposed_ideas_are_never_executed_in_same_turn(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        # The turn proposes AND has the execution leg enabled; the freshly
        # proposed idea must stay PROPOSED because approval is a human event.
        result = make_cycle_runner(cycle_service, tmp_path).run(
            snapshot_provider(snapshot(crossover_series("BTC-USD")))
        )
        (decision_id,) = result.proposer_turns[0].proposed_decision_ids
        assert result.execution.executed == ()
        assert cycle_service.get(decision_id).state is TradeIdeaState.PROPOSED

    def test_mid_turn_approval_waits_for_the_next_turn(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        # The approval CLI does not take the cycle lock, so an approval that
        # lands while the turn is running must not execute until the next
        # turn: the seam is approval BETWEEN turns.
        decision_id = "trade-20260703-cycle-race"
        cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")

        class ApprovesMidTurn:
            """Stands in for a slow proposer during which a human approves."""

            proposer_id = "test-mid-turn-approver"

            def propose(self, market_snapshot: MarketSnapshot) -> list:
                cycle_service.approve(
                    decision_id, actor_id="test-operator", reason="mid-turn approval"
                )
                return []

        runner = make_cycle_runner(cycle_service, tmp_path, proposers=[ApprovesMidTurn()])
        provider = snapshot_provider(snapshot(crossover_series("BTC-USD")))

        first = runner.run(provider)
        assert first.execution.executed == ()
        assert cycle_service.get(decision_id).state is TradeIdeaState.APPROVED

        second = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(provider)
        (executed,) = second.execution.executed
        assert executed["decision_id"] == decision_id

    def test_approval_during_snapshot_fetch_waits_for_the_next_turn(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        # The snapshot fetch is the slowest part of a real --from-coinbase
        # turn; an approval landing during it must also wait for the next
        # turn, so candidates are captured before the provider is called.
        decision_id = "trade-20260703-cycle-fetch-race"
        cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")

        def approving_provider():
            cycle_service.approve(
                decision_id, actor_id="test-operator", reason="approval during fetch"
            )
            return snapshot(crossover_series("BTC-USD")), "test:fixture:slow-fetch"

        result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(approving_provider)
        assert result.execution.executed == ()
        assert cycle_service.get(decision_id).state is TradeIdeaState.APPROVED

    def test_cancelled_during_turn_is_recorded_not_executed(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-cancel"
        cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")

        class CancelsMidTurn:
            proposer_id = "test-mid-turn-canceller"

            def propose(self, market_snapshot: MarketSnapshot) -> list:
                cycle_service.cancel(
                    decision_id, actor_id="test-operator", reason="changed my mind"
                )
                return []

        result = make_cycle_runner(cycle_service, tmp_path, proposers=[CancelsMidTurn()]).run(
            snapshot_provider(snapshot(crossover_series("BTC-USD")))
        )
        assert result.execution.executed == ()
        (skip,) = result.execution.skipped
        assert skip["decision_id"] == decision_id
        assert "state changed to cancelled" in skip["reason"]

    def test_executor_uses_the_turn_clock_not_the_wall_clock(self, tmp_path: Path) -> None:
        # A historical/offline turn runs entirely on the injected clock: an
        # idea live at the turn's own time must execute even when its expiry
        # is in the wall-clock past.
        turn_time = CYCLE_NOW - timedelta(days=400)
        historical_service = TradeIdeaService(tmp_path / "ideas", now_factory=lambda: turn_time)
        historical_service.update_budget(
            replace(
                DEFAULT_RISK_BUDGET,
                version=2,
                account_equity=Decimal("25000"),
                reason="test: attest scratch equity",
            ),
            actor_type=ActorType.HUMAN,
            actor_id="test-operator",
        )
        decision_id = "trade-20250529-cycle-clock"
        idea = replace(
            build_cycle_idea(decision_id),
            time_horizon=TimeHorizon(
                expected_hold="3-10 days",
                expires_at=turn_time + timedelta(days=7),
            ),
        )
        historical_service.propose(idea, actor_id="test-proposer")
        historical_service.approve(decision_id, actor_id="test-operator", reason="test approval")

        runner = PaperCycleRunner(
            historical_service,
            cycle_root=tmp_path / "cycle",
            proposers=[],
            broker=DeterministicBroker(),
            now_factory=lambda: turn_time,
        )
        result = runner.run(
            snapshot_provider(
                snapshot(crossover_series("BTC-USD", as_of=turn_time), as_of=turn_time)
            )
        )
        (executed,) = result.execution.executed
        assert executed["decision_id"] == decision_id


class TestExpirySweep:
    def test_stale_idea_is_swept_before_proposing(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-004"
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

        assert result.expired_decision_ids == (decision_id,)
        assert cycle_service.get(decision_id).state is TradeIdeaState.EXPIRED
