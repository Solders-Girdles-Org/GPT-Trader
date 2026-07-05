"""Event-driven lane tests (#1191): per-event kernel gating from propose to paper fill.

The lane's guarantees under test:

- with both operator gates on and audited ``bounded_autonomy``, a proposed
  idea reaches a paper fill inside one ``process`` call, with every step on
  the idea-level audit trail;
- each operator gate off degrades to the corresponding batch behavior
  (queued for review / approved awaiting the batch executor) — never a
  silent drop;
- a kernel denial at either boundary is recorded as an audited event, and a
  mid-stream autonomy drop (ratchet or kill-switch landing between propose
  and execute) denies execution on that same event, not the next cycle.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import (
    AUTO_EXECUTION_ENV_VAR,
    EVENT_LANE_ACTOR_ID,
    EventDrivenIdeaLane,
    EventLaneStage,
)
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AutonomyMode,
    TimeHorizon,
    TradeIdea,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
)

_NOW = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
_MARK = Decimal("61000")


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    trade_idea_service = TradeIdeaService(tmp_path / "ideas", now_factory=lambda: _NOW)
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


@pytest.fixture
def lane(service: TradeIdeaService) -> EventDrivenIdeaLane:
    return EventDrivenIdeaLane(
        service,
        DeterministicBroker(),
        now_factory=lambda: _NOW,
    )


def _build_idea(decision_id: str) -> TradeIdea:
    return build_trade_idea(
        decision_id=decision_id,
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=_NOW + timedelta(days=7),
        ),
    )


def _proposed_view(service: TradeIdeaService, decision_id: str) -> TradeIdeaView:
    return service.propose(_build_idea(decision_id), actor_id="strategy-signal-test")


def _enter_bounded_autonomy(service: TradeIdeaService) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="Test: enter bounded autonomy for the event lane",
    )


def _enable_stage2_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")


class TestHappyPath:
    def test_buy_idea_is_approved_and_paper_executed_in_one_call(
        self,
        service: TradeIdeaService,
        lane: EventDrivenIdeaLane,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_stage2_gates(monkeypatch)
        _enter_bounded_autonomy(service)
        view = _proposed_view(service, "trade-20260705-lane-happy")

        outcome = lane.process(view, mark=_MARK)

        assert outcome.stage is EventLaneStage.EXECUTED
        assert outcome.execution is not None
        assert outcome.execution.fill_price == _MARK
        final = service.get(view.idea.decision_id)
        assert final.state is TradeIdeaState.FILLED

    def test_every_step_lands_on_the_idea_audit_trail(
        self,
        service: TradeIdeaService,
        lane: EventDrivenIdeaLane,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_stage2_gates(monkeypatch)
        _enter_bounded_autonomy(service)
        view = _proposed_view(service, "trade-20260705-lane-trail")

        lane.process(view, mark=_MARK)

        events = service.get(view.idea.decision_id).events
        assert [event.action for event in events] == [
            AuditAction.PROPOSED,
            AuditAction.APPROVED,
            AuditAction.SUBMITTED,
            AuditAction.FILLED,
        ]
        approval = events[1]
        assert approval.actor_type is ActorType.SYSTEM
        assert approval.actor_id == EVENT_LANE_ACTOR_ID
        assert any("autonomy_state version" in entry for entry in approval.evidence)
        submission = events[2]
        assert submission.actor_id == EVENT_LANE_ACTOR_ID
        assert any(f"{AUTO_EXECUTION_ENV_VAR}=enabled" in entry for entry in submission.evidence)


class TestOperatorGates:
    def test_auto_approval_flag_off_leaves_idea_queued(
        self,
        service: TradeIdeaService,
        lane: EventDrivenIdeaLane,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(AUTO_APPROVAL_ENV_VAR, raising=False)
        monkeypatch.delenv(AUTO_EXECUTION_ENV_VAR, raising=False)
        _enter_bounded_autonomy(service)
        view = _proposed_view(service, "trade-20260705-lane-queued")

        outcome = lane.process(view, mark=_MARK)

        assert outcome.stage is EventLaneStage.QUEUED
        final = service.get(view.idea.decision_id)
        assert final.state is TradeIdeaState.PROPOSED
        assert [event.action for event in final.events] == [AuditAction.PROPOSED]

    def test_auto_execution_flag_off_leaves_idea_approved_for_batch_executor(
        self,
        service: TradeIdeaService,
        lane: EventDrivenIdeaLane,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
        monkeypatch.delenv(AUTO_EXECUTION_ENV_VAR, raising=False)
        _enter_bounded_autonomy(service)
        view = _proposed_view(service, "trade-20260705-lane-approved-only")

        outcome = lane.process(view, mark=_MARK)

        assert outcome.stage is EventLaneStage.APPROVED
        final = service.get(view.idea.decision_id)
        assert final.state is TradeIdeaState.APPROVED
        assert not any(event.action is AuditAction.SUBMITTED for event in final.events)


class TestKernelDenials:
    def test_approval_denied_outside_bounded_autonomy_is_audited(
        self,
        service: TradeIdeaService,
        lane: EventDrivenIdeaLane,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_stage2_gates(monkeypatch)
        view = _proposed_view(service, "trade-20260705-lane-approval-denied")

        outcome = lane.process(view, mark=_MARK)

        assert outcome.stage is EventLaneStage.APPROVAL_DENIED
        assert any("human_approved_execution" in violation for violation in outcome.violations)
        final = service.get(view.idea.decision_id)
        assert final.state is TradeIdeaState.PROPOSED
        skip = final.events[-1]
        assert skip.action is AuditAction.AUTO_APPROVAL_SKIPPED
        assert skip.actor_id == EVENT_LANE_ACTOR_ID

    def test_mode_drop_between_propose_and_execute_denies_execution_and_audits(
        self,
        service: TradeIdeaService,
        lane: EventDrivenIdeaLane,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The issue's kill-switch/ratchet contract: a mode drop landing after
        the approval but before execution takes effect on this event."""
        _enable_stage2_gates(monkeypatch)
        _enter_bounded_autonomy(service)
        view = _proposed_view(service, "trade-20260705-lane-kill-switch")

        original_record_approval = service.kernel.record_approval

        def approve_then_drop_mode(*args: object, **kwargs: object) -> None:
            original_record_approval(*args, **kwargs)
            service.set_autonomy_mode(
                AutonomyMode.HUMAN_APPROVED_EXECUTION,
                actor_type=ActorType.HUMAN,
                actor_id="test-operator",
                reason="Test: kill-switch lands mid-event",
            )

        monkeypatch.setattr(service.kernel, "record_approval", approve_then_drop_mode)

        outcome = lane.process(view, mark=_MARK)

        assert outcome.stage is EventLaneStage.EXECUTION_DENIED
        assert any("bounded_autonomy" in violation for violation in outcome.violations)
        final = service.get(view.idea.decision_id)
        assert final.state is TradeIdeaState.APPROVED
        skip = final.events[-1]
        assert skip.action is AuditAction.AUTO_EXECUTION_SKIPPED
        assert skip.actor_id == EVENT_LANE_ACTOR_ID
        assert any("bounded_autonomy" in entry for entry in skip.evidence)
        assert not any(event.action is AuditAction.SUBMITTED for event in final.events)

    def test_executor_refusal_after_admitted_check_is_surfaced_not_masked(
        self,
        service: TradeIdeaService,
        lane: EventDrivenIdeaLane,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A refusal in the executor's own admission window (after the lane's
        kernel check) returns a typed outcome; the idea is never left in a
        half-executed state."""
        _enable_stage2_gates(monkeypatch)
        _enter_bounded_autonomy(service)
        view = _proposed_view(service, "trade-20260705-lane-race")

        original_check_execution = service.kernel.check_execution

        def admit_then_drop_mode(*args: object, **kwargs: object):
            check = original_check_execution(*args, **kwargs)
            service.set_autonomy_mode(
                AutonomyMode.HUMAN_APPROVED_EXECUTION,
                actor_type=ActorType.HUMAN,
                actor_id="test-operator",
                reason="Test: mode drops inside the executor admission window",
            )
            return check

        monkeypatch.setattr(service.kernel, "check_execution", admit_then_drop_mode)

        outcome = lane.process(view, mark=_MARK)

        assert outcome.stage is EventLaneStage.EXECUTION_REFUSED
        final = service.get(view.idea.decision_id)
        assert final.state is TradeIdeaState.APPROVED
        assert not any(event.action is AuditAction.SUBMITTED for event in final.events)


def test_outcome_to_dict_is_json_shaped(
    service: TradeIdeaService,
    lane: EventDrivenIdeaLane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_stage2_gates(monkeypatch)
    _enter_bounded_autonomy(service)
    view = _proposed_view(service, "trade-20260705-lane-dict")

    payload = lane.process(view, mark=_MARK).to_dict()

    assert payload["stage"] == "executed"
    assert payload["decision_id"] == view.idea.decision_id
    assert payload["execution"]["fill_price"] == str(_MARK)
