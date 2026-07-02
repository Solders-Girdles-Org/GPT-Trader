from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader.features.trade_ideas import (
    ActorType,
    AuditIntegrityError,
    CloseoutResolution,
    MaxLoss,
    TimeHorizon,
)
from gpt_trader.features.trade_ideas.report import build_trade_idea_track_record_report
from gpt_trader.features.trade_ideas.service import TradeIdeaService
from tests.unit.gpt_trader.cli.commands.conftest import attest_account_equity
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _idea(decision_id: str, *, expires_at: datetime | None = None, **overrides: Any) -> Any:
    return build_trade_idea(
        decision_id=decision_id,
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=expires_at or datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
        ),
        **overrides,
    )


def test_windowed_report_ignores_lifecycle_after_cutoff(tmp_path: Path) -> None:
    root = tmp_path / "ideas"
    current_time = [datetime(2026, 5, 30, 12, 0, tzinfo=UTC)]
    service = TradeIdeaService(root, now_factory=lambda: current_time[0])

    idea = _idea("trade-window-late-fill")
    service.propose(idea, actor_id="idea-generator-v1")
    current_time[0] = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    attest_account_equity(service)
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(idea.decision_id, actor_id="operator", venue="manual")
    service.record_fill(idea.decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        idea.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("42.00"),
        realized_profit_loss_percent=Decimal("1.5"),
    )

    report = build_trade_idea_track_record_report(
        service,
        now=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        since=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        until=datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
    )

    assert report["source"] == {
        "audit_event_count": 1,
        "closeout_count": 0,
        "idea_count": 1,
    }
    assert report["workflow"]["event_counts"]["proposed"] == 1
    assert report["workflow"]["event_counts"]["approved"] == 0
    assert report["workflow"]["event_counts"]["filled"] == 0
    assert report["workflow"]["current_state_counts"]["proposed"] == 1
    assert report["workflow"]["current_state_counts"]["filled"] == 0
    assert report["workflow"]["ever_approved_count"] == 0
    assert report["workflow"]["ever_filled_count"] == 0
    assert report["closeouts"]["terminal_count"] == 0
    assert report["closeouts"]["with_closeout_count"] == 0
    assert report["closeouts"]["resolution_counts"]["thesis_target"] == 0
    assert report["closeouts"]["realized_profit_loss"]["total_amount"] == "0"
    may = report["proposal_volume"]["by_month"]["2026-05"]
    assert may["approved_count"] == 0
    assert may["terminal_count"] == 0
    assert may["with_closeout_count"] == 0
    assert may["realized_profit_loss_amount"] == "0"


def test_windowed_report_uses_cutoff_for_approval_readiness(tmp_path: Path) -> None:
    root = tmp_path / "ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )
    attest_account_equity(service)
    service.propose(
        _idea(
            "trade-window-cutoff-readiness",
            expires_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        ),
        actor_id="idea-generator-v1",
    )

    report = build_trade_idea_track_record_report(
        service,
        now=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        since=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        until=datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
    )

    assert report["quality"]["approval_ready_count"] == 1
    assert report["quality"]["approval_policy_violation_counts"] == {}


def test_report_readiness_uses_active_budget_not_defaults(tmp_path: Path) -> None:
    root = tmp_path / "ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )
    attest_account_equity(service)
    service.propose(
        _idea(
            "trade-active-budget-readiness",
            expires_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        ),
        actor_id="idea-generator-v1",
    )
    # Tighten the open-notional cap below the idea's share of attested equity
    # (6075 of 20000 ≈ 30.4%): approve() would refuse, so the report must too.
    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            max_open_notional_pct=Decimal("20"),
            reason="Tighten cap below the proposed idea's exposure",
        ),
        ActorType.HUMAN,
        "rj",
    )

    report = build_trade_idea_track_record_report(
        service,
        now=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )

    assert report["quality"]["approval_ready_count"] == 0
    assert any(
        "max_open_notional_pct" in violation
        for violation in report["quality"]["approval_policy_violation_counts"]
    )


def test_windowed_report_loads_record_version_for_last_in_window_event(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ideas"
    current_time = [datetime(2026, 5, 20, 12, 0, tzinfo=UTC)]
    service = TradeIdeaService(root, now_factory=lambda: current_time[0])
    original = _idea(
        "trade-window-versioned-record",
        max_loss=MaxLoss(),
    )
    service.propose(original, actor_id="idea-generator-v1")

    current_time[0] = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    service.request_changes(original.decision_id, actor_id="rj", reason="Add max loss")
    service.resubmit(
        _idea(
            original.decision_id,
            max_loss=MaxLoss(amount=Decimal("300"), percent_of_account=Decimal("2.0")),
        ),
        actor_id="idea-generator-v1",
    )

    report = build_trade_idea_track_record_report(
        service,
        now=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        since=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        until=datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
    )

    assert report["source"]["audit_event_count"] == 1
    assert report["workflow"]["current_state_counts"]["proposed"] == 1
    assert report["quality"]["missing_field_counts"]["max_loss.amount"] == 1
    assert report["quality"]["missing_field_counts"]["max_loss.percent_of_account"] == 1
    assert report["quality"]["approval_ready_count"] == 0


def test_windowed_report_rejects_tampered_historical_record_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )
    original = _idea("trade-window-tampered-history", max_loss=MaxLoss())
    service.propose(original, actor_id="idea-generator-v1")
    proposed_hash = service.audit_log.read_events(original.decision_id)[0].record_hash
    tampered = _idea(
        original.decision_id,
        max_loss=MaxLoss(amount=Decimal("300"), percent_of_account=Decimal("2.0")),
    )
    historical_path = root / "records" / original.decision_id / f"{proposed_hash}.json"
    historical_path.write_text(
        json.dumps(tampered.to_dict(), sort_keys=True, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(AuditIntegrityError, match="hashes to"):
        build_trade_idea_track_record_report(
            service,
            now=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            since=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
        )


def test_windowed_report_rejects_historical_record_missing_required_field(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )
    original = _idea("trade-window-missing-history-field", max_loss=MaxLoss())
    service.propose(original, actor_id="idea-generator-v1")
    proposed_hash = service.audit_log.read_events(original.decision_id)[0].record_hash
    historical_payload = original.to_dict()
    del historical_payload["instrument"]
    historical_path = root / "records" / original.decision_id / f"{proposed_hash}.json"
    historical_path.write_text(
        json.dumps(historical_payload, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(
        AuditIntegrityError, match="missing required field 'instrument'"
    ) as exc_info:
        build_trade_idea_track_record_report(
            service,
            now=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            since=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC),
        )

    assert isinstance(exc_info.value.__cause__, KeyError)
    assert exc_info.value.context["field"] == "record_hash"
    assert exc_info.value.context["value"] == proposed_hash
    assert exc_info.value.context["missing_field"] == "instrument"
