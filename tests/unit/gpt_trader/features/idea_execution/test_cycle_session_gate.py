"""Session gating of the paper-cycle proposer leg (issue #1232).

The Stage-2 turn runs on a fixed schedule regardless of market sessions, so
equity instruments must be skipped loudly — with the decision on the manifest
row — whenever XNYS is closed, while crypto instruments proceed every turn.
A market-closed skip must never mark an instrument busy: the gate blocks this
turn only, not later proposers or later turns.

Session fixtures reuse the historical facts pinned in
tests/unit/gpt_trader/core/test_trading_calendar.py: 2026-07-03 (the module's
``CYCLE_NOW`` date) is the observed Independence Day holiday, and Monday
2026-07-06 13:30-20:00 UTC is a regular XNYS session.
"""

from __future__ import annotations

from datetime import UTC, datetime
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

from gpt_trader.features.idea_execution import busy_instruments
from gpt_trader.features.trade_ideas import MarketSnapshot, TradeIdea, TradeIdeaService

# CYCLE_NOW (2026-07-03 12:00 UTC) is already a closed XNYS instant: the
# observed Independence Day holiday. A regular open instant for contrast:
XNYS_OPEN_NOW = datetime(2026, 7, 6, 18, 0, tzinfo=UTC)


class FixedProposer:
    """Emits a fixed candidate list regardless of the snapshot."""

    def __init__(self, proposer_id: str, ideas: list[TradeIdea]) -> None:
        self.proposer_id = proposer_id
        self._ideas = ideas

    def propose(self, market_snapshot: MarketSnapshot) -> list[TradeIdea]:
        return list(self._ideas)


class TestProposerSessionGate:
    def test_equity_candidate_is_skipped_when_xnys_is_closed(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        proposer = FixedProposer(
            "test-mixed-proposer",
            [
                build_cycle_idea("trade-20260703-gate-aapl", instrument="AAPL"),
                build_cycle_idea("trade-20260703-gate-btc", instrument="BTC-USD"),
            ],
        )
        result = make_cycle_runner(cycle_service, tmp_path, proposers=[proposer]).run(
            snapshot_provider(snapshot(flat_series("BTC-USD")))
        )

        (proposer_turn,) = result.proposer_turns
        # Crypto proceeds every turn; the equity candidate waits for the open.
        assert proposer_turn.proposal_count == 1
        assert proposer_turn.proposed_decision_ids == ("trade-20260703-gate-btc",)
        (skip,) = proposer_turn.skipped_closed_sessions
        assert skip["instrument"] == "AAPL"
        assert "market closed for session XNYS" in skip["reason"]
        assert "next open 2026-07-06T13:30:00+00:00" in skip["reason"]
        # The skip left no record behind: nothing to expire, nothing busy.
        assert [view.idea.instrument for view in cycle_service.list_views()] == ["BTC-USD"]

    def test_equity_candidate_is_proposed_during_xnys_session(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        proposer = FixedProposer(
            "test-equity-proposer",
            [build_cycle_idea("trade-20260706-gate-open", instrument="AAPL")],
        )
        result = make_cycle_runner(
            cycle_service, tmp_path, proposers=[proposer], now=XNYS_OPEN_NOW
        ).run(snapshot_provider(snapshot(flat_series("AAPL"))))

        (proposer_turn,) = result.proposer_turns
        assert proposer_turn.proposal_count == 1
        assert proposer_turn.skipped_closed_sessions == ()

    def test_closed_session_skip_does_not_mark_the_instrument_busy(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        # Two proposers emit the same equity instrument in one closed-market
        # turn: the second must see another closed-session skip, not an
        # "instrument already has an open idea" block from the first.
        first = FixedProposer(
            "test-first-proposer",
            [build_cycle_idea("trade-20260703-gate-first", instrument="AAPL")],
        )
        second = FixedProposer(
            "test-second-proposer",
            [build_cycle_idea("trade-20260703-gate-second", instrument="AAPL")],
        )
        result = make_cycle_runner(cycle_service, tmp_path, proposers=[first, second]).run(
            snapshot_provider(snapshot(flat_series("BTC-USD")))
        )

        for proposer_turn in result.proposer_turns:
            assert proposer_turn.skipped_open_instruments == ()
            (skip,) = proposer_turn.skipped_closed_sessions
            assert skip["instrument"] == "AAPL"
        assert busy_instruments(cycle_service) == {}

        # The next turn, at the open, proposes the instrument normally.
        reopened = make_cycle_runner(
            cycle_service, tmp_path, proposers=[second], now=XNYS_OPEN_NOW
        ).run(snapshot_provider(snapshot(flat_series("AAPL"))))
        assert reopened.proposer_turns[0].proposed_decision_ids == ("trade-20260703-gate-second",)

    def test_unclassifiable_instrument_is_skipped_loudly(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        proposer = FixedProposer(
            "test-odd-proposer",
            [build_cycle_idea("trade-20260703-gate-odd", instrument="BTC-USD-PERP")],
        )
        result = make_cycle_runner(cycle_service, tmp_path, proposers=[proposer]).run(
            snapshot_provider(snapshot(flat_series("BTC-USD")))
        )

        (proposer_turn,) = result.proposer_turns
        assert proposer_turn.proposal_count == 0
        (skip,) = proposer_turn.skipped_closed_sessions
        assert skip["instrument"] == "BTC-USD-PERP"
        assert "not classifiable" in skip["reason"]

    def test_calendar_out_of_bounds_is_skipped_loudly(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        proposer = FixedProposer(
            "test-historical-proposer",
            [build_cycle_idea("trade-20260703-gate-historical", instrument="AAPL")],
        )
        historical_now = datetime(1980, 1, 2, 15, 0, tzinfo=UTC)
        result = make_cycle_runner(
            cycle_service, tmp_path, proposers=[proposer], now=historical_now
        ).run(snapshot_provider(snapshot(flat_series("AAPL"))))

        (skip,) = result.proposer_turns[0].skipped_closed_sessions
        assert skip["instrument"] == "AAPL"
        assert "session calendar XNYS cannot evaluate" in skip["reason"]
        assert cycle_service.list_views() == []


class TestExecutionLegSessionGuard:
    def test_approved_equity_idea_waits_for_the_open(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-gate-approved"
        cycle_service.propose(
            build_cycle_idea(decision_id, instrument="AAPL"), actor_id="test-proposer"
        )
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")

        # Closed turn: the executor's typed refusal lands as an execution skip.
        closed_turn = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
            snapshot_provider(snapshot(flat_series("AAPL")))
        )
        assert closed_turn.execution.executed == ()
        (skip,) = closed_turn.execution.skipped
        assert skip["decision_id"] == decision_id
        assert "market closed for session XNYS" in skip["reason"]
        assert cycle_service.get(decision_id).state.value == "approved"

        # Open turn: the fill resumes against this turn's own snapshot marks.
        open_turn = make_cycle_runner(cycle_service, tmp_path, proposers=[], now=XNYS_OPEN_NOW).run(
            snapshot_provider(snapshot(flat_series("AAPL")))
        )
        (executed,) = open_turn.execution.executed
        assert executed["decision_id"] == decision_id

    def test_filled_equity_idea_exit_skip_is_manifest_visible(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-gate-filled"
        cycle_service.propose(
            build_cycle_idea(decision_id, instrument="AAPL"), actor_id="test-proposer"
        )
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")
        cycle_service.record_submission(decision_id, actor_id="test-executor", venue="paper")
        cycle_service.record_fill(decision_id, actor_id="test-venue", venue="paper")

        result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
            snapshot_provider(snapshot(flat_series("AAPL")))
        )

        assert result.resolved_decision_ids == ()
        (skip,) = result.exit_monitor_skipped_closed_sessions
        assert skip["decision_id"] == decision_id
        assert "market closed for session XNYS" in skip["reason"]
        (row,) = manifest_rows(tmp_path)
        assert row["exit_monitor_skipped_closed_sessions"] == [dict(skip)]


class TestManifestSessionEvidence:
    def test_manifest_row_records_the_session_decision_per_instrument(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
            snapshot_provider(snapshot(flat_series("AAPL"), flat_series("BTC-USD")))
        )

        decisions = {decision["instrument"]: decision for decision in result.session_gate}
        assert decisions["BTC-USD"]["session"] == "24x7"
        assert decisions["BTC-USD"]["open"] is True
        assert decisions["AAPL"]["session"] == "XNYS"
        assert decisions["AAPL"]["open"] is False
        assert "next open 2026-07-06T13:30:00+00:00" in decisions["AAPL"]["reason"]

        (row,) = manifest_rows(tmp_path)
        assert row["session_gate"] == [dict(decision) for decision in result.session_gate]
        assert row["started_at"] == CYCLE_NOW.isoformat()
