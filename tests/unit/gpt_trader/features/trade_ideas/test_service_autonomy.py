from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AutonomyIntegrityError,
    AutonomyMode,
    AutonomyStateEntry,
    AutonomyStateLog,
    CloseoutResolution,
    PolicyViolationError,
    RiskBudget,
    TradeIdeaState,
)
from gpt_trader.features.trade_ideas.autonomy import (
    AUTONOMY_SOURCE_LOG,
    AUTONOMY_SOURCE_SEEDED_DEFAULT,
    RATCHET_ACTOR_ID,
)
from gpt_trader.features.trade_ideas.service import TradeIdeaService


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def _autonomy_log_path(tmp_path: Path) -> Path:
    return tmp_path / "trade_ideas" / "autonomy_state.jsonl"


def _corrupt_autonomy_log(tmp_path: Path) -> None:
    path = _autonomy_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage\n", encoding="utf-8")


def _enter_bounded_autonomy(service: TradeIdeaService) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Test: enter bounded autonomy through the audited path",
    )


def _record_same_day_realized_loss(
    service: TradeIdeaService,
    *,
    decision_id: str,
    loss_percent: str,
) -> None:
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(decision_id, actor_id="operator", venue="manual")
    service.record_fill(decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_percent=Decimal(loss_percent),
    )


def test_peek_autonomy_does_not_seed_the_log(service: TradeIdeaService, tmp_path: Path) -> None:
    resolution = service.peek_autonomy()

    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert resolution.source == AUTONOMY_SOURCE_SEEDED_DEFAULT
    assert not _autonomy_log_path(tmp_path).exists()


def test_current_autonomy_seeds_default_on_first_use(
    service: TradeIdeaService, tmp_path: Path
) -> None:
    resolution = service.current_autonomy()

    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert resolution.version == 1
    assert resolution.source == AUTONOMY_SOURCE_LOG
    history = service.autonomy_history()
    assert len(history) == 1
    assert history[0].actor_type is ActorType.SYSTEM
    assert history[0].actor_id == "seed-defaults"


def test_current_autonomy_adopts_concurrent_seed(
    service: TradeIdeaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_append = AutonomyStateLog.append

    def racing_append(self: AutonomyStateLog, entry: AutonomyStateEntry) -> None:
        # A concurrent process seeds first, so this append loses the version
        # race under the log lock instead of duplicating version 1.
        winner = AutonomyStateLog(self.path)
        real_append(
            winner,
            AutonomyStateEntry(
                version=1,
                timestamp=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
                mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
                actor_type=ActorType.SYSTEM,
                actor_id="other-process",
                reason="Seeded default from a concurrent process",
            ),
        )
        real_append(self, entry)

    monkeypatch.setattr(AutonomyStateLog, "append", racing_append)

    resolution = service.current_autonomy()

    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert resolution.version == 1
    assert resolution.source == AUTONOMY_SOURCE_LOG
    history = service.autonomy_history()
    assert [entry.version for entry in history] == [1]
    assert history[0].actor_id == "other-process"


def test_human_raise_is_audited_and_becomes_current(service: TradeIdeaService) -> None:
    _enter_bounded_autonomy(service)

    resolution = service.current_autonomy()
    assert resolution.mode is AutonomyMode.BOUNDED_AUTONOMY
    assert resolution.version == 2
    latest = service.autonomy_history()[-1]
    assert latest.actor_type is ActorType.HUMAN
    assert latest.reason.startswith("Test: enter bounded autonomy")


def test_non_human_raise_is_refused(service: TradeIdeaService) -> None:
    with pytest.raises(PolicyViolationError) as exc_info:
        service.set_autonomy_mode(
            AutonomyMode.BOUNDED_AUTONOMY,
            actor_type=ActorType.AI,
            actor_id="idea-generator-v1",
            reason="Agent asking for more autonomy",
        )

    assert any("requires a human actor" in violation for violation in exc_info.value.violations)
    assert service.current_autonomy().mode is AutonomyMode.HUMAN_APPROVED_EXECUTION


def test_any_actor_may_lower_the_level(service: TradeIdeaService) -> None:
    _enter_bounded_autonomy(service)

    service.set_autonomy_mode(
        AutonomyMode.RESEARCH_ONLY,
        actor_type=ActorType.SYSTEM,
        actor_id="kill-switch-listener",
        reason="Kill switch tripped; halting all approvals",
    )

    assert service.current_autonomy().mode is AutonomyMode.RESEARCH_ONLY


def test_autonomy_change_requires_rationale(service: TradeIdeaService) -> None:
    with pytest.raises(ValueError, match="reason"):
        service.set_autonomy_mode(
            AutonomyMode.RESEARCH_ONLY,
            actor_type=ActorType.HUMAN,
            actor_id="rj",
            reason="   ",
        )


def test_mode_change_applies_to_next_decision_without_service_rebuild(
    service: TradeIdeaService,
) -> None:
    """The reload boundary is the next decision, not service construction."""
    attest_account_equity(service)
    idea = build_trade_idea(decision_id="trade-20260612-mode")
    service.propose(idea, actor_id="idea-generator-v1")

    service.set_autonomy_mode(
        AutonomyMode.RESEARCH_ONLY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Pause approvals during incident review",
    )

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    assert any("research_only" in violation for violation in exc_info.value.violations)

    service.set_autonomy_mode(
        AutonomyMode.HUMAN_APPROVED_EXECUTION,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Incident resolved; resume human-approved execution",
    )

    approved = service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    assert approved.state is TradeIdeaState.APPROVED


def test_broken_log_fails_approvals_closed_to_research_only(
    service: TradeIdeaService, tmp_path: Path
) -> None:
    attest_account_equity(service)
    idea = build_trade_idea(decision_id="trade-20260612-closed-log")
    service.propose(idea, actor_id="idea-generator-v1")
    _corrupt_autonomy_log(tmp_path)

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    violations = exc_info.value.violations
    assert any("failed closed" in violation for violation in violations)
    assert any("research_only" in violation for violation in violations)


def test_broken_log_refuses_mode_changes(service: TradeIdeaService, tmp_path: Path) -> None:
    _corrupt_autonomy_log(tmp_path)

    with pytest.raises(AutonomyIntegrityError, match="repair it"):
        service.set_autonomy_mode(
            AutonomyMode.RESEARCH_ONLY,
            actor_type=ActorType.HUMAN,
            actor_id="rj",
            reason="Attempted change over a broken log",
        )


def test_broken_log_refuses_budget_changes(service: TradeIdeaService, tmp_path: Path) -> None:
    service.current_budget()
    _corrupt_autonomy_log(tmp_path)
    widened = RiskBudget.from_dict(
        {**DEFAULT_RISK_BUDGET.to_dict(), "version": 2, "max_loss_per_idea_pct": "8"}
    )

    with pytest.raises(PolicyViolationError) as exc_info:
        service.update_budget(widened, actor_type=ActorType.HUMAN, actor_id="rj")

    assert any("failed closed" in violation for violation in exc_info.value.violations)


def test_daily_loss_breach_ratchets_bounded_autonomy_down(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    _record_same_day_realized_loss(service, decision_id="trade-20260612-loss", loss_percent="-12")
    candidate = build_trade_idea(decision_id="trade-20260612-next")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError):
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    resolution = service.current_autonomy()
    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    latest = service.autonomy_history()[-1]
    assert latest.actor_type is ActorType.SYSTEM
    assert latest.actor_id == RATCHET_ACTOR_ID
    assert latest.evidence
    assert "same_day_realized_loss_pct=12" in latest.evidence[0]
    assert "trading_day=2026-06-12" in latest.evidence[0]


def test_ratchet_fires_at_budget_decision_boundary(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    _record_same_day_realized_loss(service, decision_id="trade-20260612-loss", loss_percent="-12")

    current = service.current_budget()
    tightened = RiskBudget.from_dict(
        {
            **current.to_dict(),
            "version": current.version + 1,
            "max_loss_per_idea_pct": "2",
            "reason": "Tighten after the breach",
        }
    )
    service.update_budget(tightened, actor_type=ActorType.HUMAN, actor_id="rj")

    resolution = service.current_autonomy()
    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert service.autonomy_history()[-1].actor_id == RATCHET_ACTOR_ID


def test_tightening_daily_cap_below_realized_losses_ratchets_in_the_same_call(
    service: TradeIdeaService,
) -> None:
    """Enacting a budget is a decision: the ratchet must see the new cap now."""
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    _record_same_day_realized_loss(service, decision_id="trade-20260612-loss", loss_percent="-8")
    assert service.current_autonomy().mode is AutonomyMode.BOUNDED_AUTONOMY

    current = service.current_budget()
    tightened = RiskBudget.from_dict(
        {
            **current.to_dict(),
            "version": current.version + 1,
            "max_daily_loss_pct": "5",
            "reason": "Tighten the daily cap below today's realized losses",
        }
    )
    service.update_budget(tightened, actor_type=ActorType.HUMAN, actor_id="rj")

    resolution = service.current_autonomy()
    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    latest = service.autonomy_history()[-1]
    assert latest.actor_id == RATCHET_ACTOR_ID
    assert "max_daily_loss_pct=5" in latest.evidence[0]
    assert f"risk budget version {tightened.version}" in latest.evidence[0]


def test_no_ratchet_without_a_breach(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    _record_same_day_realized_loss(
        service, decision_id="trade-20260612-small-loss", loss_percent="-1"
    )
    candidate = build_trade_idea(decision_id="trade-20260612-next")
    service.propose(candidate, actor_id="idea-generator-v1")

    approved = service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert approved.state is TradeIdeaState.APPROVED
    assert service.current_autonomy().mode is AutonomyMode.BOUNDED_AUTONOMY
    assert all(entry.actor_id != RATCHET_ACTOR_ID for entry in service.autonomy_history())


def test_ratchet_does_not_fire_below_bounded_autonomy(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    _record_same_day_realized_loss(service, decision_id="trade-20260612-loss", loss_percent="-12")
    candidate = build_trade_idea(decision_id="trade-20260612-next")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError):
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert service.current_autonomy().mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert all(entry.actor_id != RATCHET_ACTOR_ID for entry in service.autonomy_history())
