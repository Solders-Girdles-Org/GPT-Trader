from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    ActorType,
    AuditAction,
    CloseoutAttributionIntegrityError,
    CloseoutResolution,
    DuplicateCloseoutAttributionError,
    InvalidTransitionError,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
    UnknownTradeIdeaError,
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(built)
    return built


def _write_closeout_payload(
    service: TradeIdeaService,
    view: TradeIdeaView,
    **overrides: object,
) -> None:
    max_loss = view.idea.max_loss
    payload: dict[str, object] = {
        "decision_id": view.idea.decision_id,
        "timestamp": "2026-06-12T10:05:00+00:00",
        "actor_type": "human",
        "actor_id": "rj",
        "terminal_event_id": view.events[-1].event_id,
        "record_hash": view.events[-1].record_hash,
        "resolution": CloseoutResolution.EXPIRY.value,
        "realized_profit_loss_amount": None,
        "realized_profit_loss_percent": None,
        "realized_profit_loss_unavailable_reason": "Idea expired before entry fill",
        "max_loss": {
            "amount": str(max_loss.amount) if max_loss.amount is not None else None,
            "percent_of_account": (
                str(max_loss.percent_of_account)
                if max_loss.percent_of_account is not None
                else None
            ),
            "assumptions": list(max_loss.assumptions),
        },
        "evidence": [],
    }
    payload.update(overrides)
    service.closeout_log.path.parent.mkdir(parents=True, exist_ok=True)
    service.closeout_log.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_record_closeout_attribution_for_filled_idea(service: TradeIdeaService) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Thesis and risk verified")
    service.record_submission(idea.decision_id, actor_id="executor", venue="coinbase")
    filled = service.record_fill(
        idea.decision_id,
        actor_id="coinbase",
        venue="coinbase",
        external_order_id="abc-123",
    )

    closeout = service.record_closeout_attribution(
        idea.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("125.50"),
        realized_profit_loss_percent=Decimal("2.4"),
        evidence=("broker-statement:abc-123", "chart:target-hit"),
    )

    assert closeout.decision_id == idea.decision_id
    assert closeout.actor_type == ActorType.HUMAN.value
    assert closeout.terminal_event_id == filled.events[-1].event_id
    assert closeout.record_hash == filled.events[-1].record_hash
    assert closeout.max_loss.amount == idea.max_loss.amount
    assert closeout.max_loss.percent_of_account == idea.max_loss.percent_of_account
    assert closeout.evidence == ("broker-statement:abc-123", "chart:target-hit")
    view = service.get(idea.decision_id)
    assert view.state is TradeIdeaState.FILLED
    assert view.closeout_attribution == closeout
    assert [event.action for event in view.events] == [
        AuditAction.PROPOSED,
        AuditAction.APPROVED,
        AuditAction.SUBMITTED,
        AuditAction.FILLED,
    ]


def test_record_closeout_attribution_for_expired_idea_without_profit_loss(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    expired = service.expire(idea.decision_id)

    closeout = service.record_closeout_attribution(
        idea.decision_id,
        actor_id="expiry-sweep",
        actor_type=ActorType.SYSTEM,
        resolution="expiry",
        realized_profit_loss_unavailable_reason="Idea expired before entry fill",
        evidence=("expiry-sweep:2026-06-12",),
    )

    assert closeout.actor_type == ActorType.SYSTEM.value
    assert closeout.resolution is CloseoutResolution.EXPIRY
    assert closeout.realized_profit_loss_amount is None
    assert closeout.realized_profit_loss_percent is None
    assert closeout.realized_profit_loss_unavailable_reason == "Idea expired before entry fill"
    assert closeout.terminal_event_id == expired.events[-1].event_id


def test_record_closeout_attribution_rejects_non_terminal_idea(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")

    with pytest.raises(InvalidTransitionError, match="must be terminal"):
        service.record_closeout_attribution(
            idea.decision_id,
            actor_id="rj",
            resolution=CloseoutResolution.INVALIDATION,
            realized_profit_loss_amount=Decimal("-250"),
        )

    assert service.get_closeout_attribution(idea.decision_id) is None


def test_record_closeout_attribution_rejects_unknown_idea(
    service: TradeIdeaService,
) -> None:
    with pytest.raises(UnknownTradeIdeaError):
        service.record_closeout_attribution(
            "trade-missing",
            actor_id="rj",
            resolution=CloseoutResolution.INVALIDATION,
            realized_profit_loss_amount=Decimal("-250"),
        )


def test_record_closeout_attribution_rejects_duplicate_conflict(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.expire(idea.decision_id)
    original = service.record_closeout_attribution(
        idea.decision_id,
        actor_id="expiry-sweep",
        actor_type=ActorType.SYSTEM,
        resolution=CloseoutResolution.EXPIRY,
        realized_profit_loss_unavailable_reason="Idea expired before entry fill",
    )

    with pytest.raises(DuplicateCloseoutAttributionError):
        service.record_closeout_attribution(
            idea.decision_id,
            actor_id="rj",
            resolution=CloseoutResolution.INVALIDATION,
            realized_profit_loss_amount=Decimal("-250"),
            evidence=("manual-review",),
        )

    assert service.get_closeout_attribution(idea.decision_id) == original


def test_record_closeout_attribution_rejects_malformed_numeric_payload(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.expire(idea.decision_id)

    with pytest.raises(ValueError, match="realized_profit_loss_amount must be a finite decimal"):
        service.record_closeout_attribution(
            idea.decision_id,
            actor_id="rj",
            resolution=CloseoutResolution.INVALIDATION,
            realized_profit_loss_amount="not-a-decimal",
        )

    assert service.get_closeout_attribution(idea.decision_id) is None


def test_persisted_closeout_rejects_stale_terminal_event_id_against_current_event(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    expired = service.expire(idea.decision_id)
    _write_closeout_payload(service, expired, terminal_event_id="evt-stale")

    with pytest.raises(
        CloseoutAttributionIntegrityError,
        match="terminal_event_id expected",
    ) as get_exc_info:
        service.get(idea.decision_id)
    assert get_exc_info.value.context["field"] == "terminal_event_id"
    assert get_exc_info.value.context["stored_terminal_event_id"] == "evt-stale"
    assert get_exc_info.value.context["current_terminal_event_id"] == expired.events[-1].event_id

    with pytest.raises(CloseoutAttributionIntegrityError, match="terminal_event_id expected"):
        service.list_views()
    with pytest.raises(CloseoutAttributionIntegrityError, match="terminal_event_id expected"):
        service.get_closeout_attribution(idea.decision_id)
    with pytest.raises(CloseoutAttributionIntegrityError, match="terminal_event_id expected"):
        service.record_closeout_attribution(
            idea.decision_id,
            actor_id="rj",
            resolution=CloseoutResolution.EXPIRY,
            realized_profit_loss_unavailable_reason="Idea expired before entry fill",
        )


def test_persisted_closeout_rejects_stale_record_hash_against_current_event(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    expired = service.expire(idea.decision_id)
    _write_closeout_payload(service, expired, record_hash="stale-record-hash")

    with pytest.raises(
        CloseoutAttributionIntegrityError,
        match="record_hash expected",
    ) as get_exc_info:
        service.get(idea.decision_id)
    assert get_exc_info.value.context["field"] == "record_hash"
    assert get_exc_info.value.context["stored_record_hash"] == "stale-record-hash"
    assert get_exc_info.value.context["current_record_hash"] == expired.events[-1].record_hash

    with pytest.raises(CloseoutAttributionIntegrityError, match="record_hash expected"):
        service.list_views()
    with pytest.raises(CloseoutAttributionIntegrityError, match="record_hash expected"):
        service.get_closeout_attribution(idea.decision_id)
    with pytest.raises(CloseoutAttributionIntegrityError, match="record_hash expected"):
        service.record_closeout_attribution(
            idea.decision_id,
            actor_id="rj",
            resolution=CloseoutResolution.EXPIRY,
            realized_profit_loss_unavailable_reason="Idea expired before entry fill",
        )


@pytest.mark.parametrize(
    ("max_loss_override", "field_name", "message"),
    [
        ({"amount": "251"}, "max_loss.amount", "max_loss.amount expected '250'"),
        ({"percent_of_account": "1.6"}, "max_loss.percent_of_account", "1.5"),
        ({"assumptions": ["stale sizing note"]}, "max_loss.assumptions", "assumptions"),
    ],
)
def test_persisted_closeout_rejects_current_event_with_stale_max_loss_snapshot(
    service: TradeIdeaService,
    max_loss_override: dict[str, object],
    field_name: str,
    message: str,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    expired = service.expire(idea.decision_id)
    current_max_loss = {
        "amount": "250",
        "percent_of_account": "1.5",
        "assumptions": ["Fill at zone midpoint", "No slippage beyond 10 bps"],
    }
    _write_closeout_payload(
        service,
        expired,
        max_loss={**current_max_loss, **max_loss_override},
    )

    with pytest.raises(CloseoutAttributionIntegrityError, match=message) as get_exc_info:
        service.get(idea.decision_id)
    context = get_exc_info.value.context
    assert context["field"] == field_name
    assert context["stored_terminal_event_id"] == expired.events[-1].event_id
    assert context["current_terminal_event_id"] == expired.events[-1].event_id
    assert context["stored_record_hash"] == expired.events[-1].record_hash
    assert context["current_record_hash"] == expired.events[-1].record_hash
    assert context["stored_max_loss"] == {**current_max_loss, **max_loss_override}
    assert context["current_max_loss"] == current_max_loss


def test_persisted_closeout_log_rejects_non_object_max_loss_with_line_context(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    expired = service.expire(idea.decision_id)
    payload = {
        "decision_id": idea.decision_id,
        "timestamp": "2026-06-12T10:05:00+00:00",
        "actor_type": "human",
        "actor_id": "rj",
        "terminal_event_id": expired.events[-1].event_id,
        "record_hash": expired.events[-1].record_hash,
        "resolution": CloseoutResolution.EXPIRY.value,
        "realized_profit_loss_amount": None,
        "realized_profit_loss_percent": None,
        "realized_profit_loss_unavailable_reason": "Idea expired before entry fill",
        "max_loss": None,
        "evidence": [],
    }
    service.closeout_log.path.parent.mkdir(parents=True, exist_ok=True)
    service.closeout_log.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(
        CloseoutAttributionIntegrityError,
        match="Closeout attribution log line 1 is malformed: max_loss must be a JSON object",
    ) as log_exc_info:
        service.closeout_log.read_records()
    assert log_exc_info.value.context["field"] == "line"
    assert log_exc_info.value.context["value"] == 1

    with pytest.raises(
        CloseoutAttributionIntegrityError,
        match="Closeout attribution log line 1 is malformed: max_loss must be a JSON object",
    ):
        service.get(idea.decision_id)

    with pytest.raises(
        CloseoutAttributionIntegrityError,
        match="Closeout attribution log line 1 is malformed: max_loss must be a JSON object",
    ):
        service.list_views()


@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        ("amount", "max_loss.amount must be non-negative"),
        ("percent_of_account", "max_loss.percent_of_account must be non-negative"),
    ],
)
def test_persisted_closeout_log_rejects_negative_max_loss_values_with_line_context(
    service: TradeIdeaService,
    field_name: str,
    message: str,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    expired = service.expire(idea.decision_id)
    max_loss = {"amount": "250", "percent_of_account": "1.5", "assumptions": []}
    max_loss[field_name] = "-1"
    payload = {
        "decision_id": idea.decision_id,
        "timestamp": "2026-06-12T10:05:00+00:00",
        "actor_type": "human",
        "actor_id": "rj",
        "terminal_event_id": expired.events[-1].event_id,
        "record_hash": expired.events[-1].record_hash,
        "resolution": CloseoutResolution.EXPIRY.value,
        "realized_profit_loss_amount": None,
        "realized_profit_loss_percent": None,
        "realized_profit_loss_unavailable_reason": "Idea expired before entry fill",
        "max_loss": max_loss,
        "evidence": [],
    }
    service.closeout_log.path.parent.mkdir(parents=True, exist_ok=True)
    service.closeout_log.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    expected = f"Closeout attribution log line 1 is malformed: {message}"
    with pytest.raises(CloseoutAttributionIntegrityError, match=expected) as log_exc_info:
        service.closeout_log.read_records()
    assert log_exc_info.value.context["field"] == "line"
    assert log_exc_info.value.context["value"] == 1

    with pytest.raises(CloseoutAttributionIntegrityError, match=expected):
        service.get(idea.decision_id)
