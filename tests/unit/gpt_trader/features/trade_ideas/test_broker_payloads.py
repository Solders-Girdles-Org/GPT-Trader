from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas import (
    ActorType,
    AutonomyMode,
    InvalidTransitionError,
    PolicyViolationError,
    TimeHorizon,
    TradeDirection,
    TradeIdeaService,
    TradeIdeaState,
    canonical_ticket_json,
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(built)
    return built


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _payload_without_export_time_and_hash(payload: dict[str, object]) -> dict[str, object]:
    normalized = json.loads(canonical_ticket_json(payload))
    normalized.pop("ticket_hash")
    policy_budget_snapshot = normalized["policy_budget_snapshot"]
    assert isinstance(policy_budget_snapshot, dict)
    policy_budget_snapshot["evaluated_at"] = "<export-evaluation-time>"
    return normalized


def test_export_broker_ticket_payload_for_approved_idea(service: TradeIdeaService) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    payload = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="coinbase",
        venue_order_type="limit",
        time_in_force="GTC",
    )

    assert payload["schema_version"] == "gpt-trader.trade_idea_ticket.v1"
    assert payload["decision_id"] == idea.decision_id
    assert payload["record_hash"] == idea.record_hash()
    broker_ticket = _mapping(payload["broker_ticket"])
    venue_request = _mapping(payload["venue_request"])
    venue_payload = _mapping(payload["venue_payload"])
    policy_budget_snapshot = _mapping(payload["policy_budget_snapshot"])
    provenance = _mapping(payload["provenance"])
    approval_event = _mapping(provenance["approval_event"])
    assert broker_ticket["source_record"] == idea.broker_ticket.to_dict()
    assert broker_ticket["exported"] == {
        "venue": "coinbase",
        "status": "approved",
    }
    client_order_id = _text(venue_request["client_order_id"])
    assert client_order_id.startswith(f"gpt-trader-coinbase-{idea.decision_id}-")
    assert venue_payload["order_side"] == "buy"
    assert policy_budget_snapshot["risk_budget"] == service.current_budget().to_dict()
    assert approval_event["actor_id"] == "rj"
    assert provenance["terminal_event"] is None


@pytest.mark.parametrize(
    "direction",
    [TradeDirection.SPREAD, TradeDirection.HEDGE, TradeDirection.NO_TRADE],
)
def test_export_broker_ticket_rejects_non_orderable_direction(
    service: TradeIdeaService,
    direction: TradeDirection,
) -> None:
    idea = build_trade_idea(
        decision_id=f"trade-{direction.value.replace('_', '-')}",
        direction=direction,
    )
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    with pytest.raises(ValidationError) as exc_info:
        service.export_broker_ticket_payload(
            idea.decision_id,
            venue="coinbase",
            venue_order_type="limit",
            time_in_force="GTC",
        )

    assert exc_info.value.context["field"] == "direction"
    assert exc_info.value.context["value"] == direction.value


def test_export_broker_ticket_payload_is_canonical_and_deterministic(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    first = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )
    second = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )

    assert first == second
    assert canonical_ticket_json(first) == canonical_ticket_json(second)


def test_export_broker_ticket_payload_is_stable_only_for_fixed_export_time(
    tmp_path: Path,
) -> None:
    current_time = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)

    def now() -> datetime:
        return current_time

    service = TradeIdeaService(tmp_path / "trade_ideas", now_factory=now)
    attest_account_equity(service)
    idea = build_trade_idea(decision_id="trade-export-time-sensitive-hash")
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    first = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )
    fixed_time_repeat = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )
    current_time = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
    later_export = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )

    assert canonical_ticket_json(first) == canonical_ticket_json(fixed_time_repeat)
    assert canonical_ticket_json(first) != canonical_ticket_json(later_export)
    assert first["ticket_hash"] != later_export["ticket_hash"]
    assert (
        _mapping(first["policy_budget_snapshot"])["evaluated_at"]
        != _mapping(later_export["policy_budget_snapshot"])["evaluated_at"]
    )
    assert _payload_without_export_time_and_hash(first) == _payload_without_export_time_and_hash(
        later_export
    )


def test_export_broker_ticket_rejects_unapproved_state(service: TradeIdeaService) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")

    with pytest.raises(InvalidTransitionError) as exc_info:
        service.export_broker_ticket_payload(
            idea.decision_id,
            venue="manual",
            venue_order_type="operator_selected",
            time_in_force="operator_selected",
        )

    assert exc_info.value.context["field"] == "after_state"
    assert exc_info.value.context["value"] == TradeIdeaState.PROPOSED.value


def test_export_broker_ticket_allows_terminal_state_after_approval(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(idea.decision_id, actor_id="operator", venue="manual")
    service.record_fill(
        idea.decision_id,
        actor_id="manual",
        venue="manual",
        external_order_id="external-123",
    )

    payload = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
        client_order_id="manual-ticket-1",
    )

    broker_ticket = _mapping(payload["broker_ticket"])
    provenance = _mapping(payload["provenance"])
    terminal_event = _mapping(provenance["terminal_event"])
    assert broker_ticket["exported"] == {"venue": "manual", "status": "submitted"}
    assert terminal_event["action"] == "filled"
    assert terminal_event["external_order_id_present"] is True
    assert "external-123" not in canonical_ticket_json(payload)


def test_default_client_order_id_is_bounded_for_long_decision_id(
    service: TradeIdeaService,
) -> None:
    decision_id = f"trade-{'x' * 110}"
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    payload = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="coinbase",
        venue_order_type="limit",
        time_in_force="GTC",
    )

    venue_request = _mapping(payload["venue_request"])
    venue_payload = _mapping(payload["venue_payload"])
    client_order_id = _text(venue_request["client_order_id"])
    assert len(client_order_id) <= 128
    assert client_order_id.startswith("gpt-trader-coinbase-trade-")
    assert venue_payload["client_order_id"] == client_order_id


def test_export_broker_ticket_refuses_approved_idea_stale_at_export_time(
    tmp_path: Path,
) -> None:
    current_time = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)

    def now() -> datetime:
        return current_time

    service = TradeIdeaService(tmp_path / "trade_ideas", now_factory=now)
    attest_account_equity(service)
    idea = build_trade_idea(
        decision_id="trade-stale-at-export",
        time_horizon=TimeHorizon(
            expected_hold="1 day",
            expires_at=datetime(2026, 6, 13, 10, 0, tzinfo=UTC),
        ),
    )
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    current_time = datetime(2026, 6, 14, 10, 0, tzinfo=UTC)

    with pytest.raises(PolicyViolationError) as exc_info:
        service.export_broker_ticket_payload(
            idea.decision_id,
            venue="coinbase",
            venue_order_type="limit",
            time_in_force="GTC",
        )

    assert any("export no stale ticket" in item for item in exc_info.value.violations)
    assert any("approve nothing stale" in item for item in exc_info.value.violations)


def test_export_broker_ticket_policy_violations_use_export_time(
    tmp_path: Path,
) -> None:
    current_time = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)

    def now() -> datetime:
        return current_time

    service = TradeIdeaService(tmp_path / "trade_ideas", now_factory=now)
    attest_account_equity(service)
    idea = build_trade_idea(
        decision_id="trade-terminal-stale-policy-snapshot",
        time_horizon=TimeHorizon(
            expected_hold="1 day",
            expires_at=datetime(2026, 6, 13, 10, 0, tzinfo=UTC),
        ),
    )
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(idea.decision_id, actor_id="operator", venue="manual")
    service.record_fill(
        idea.decision_id,
        actor_id="manual",
        venue="manual",
        external_order_id="terminal-fill-123",
    )
    export_time = datetime(2026, 6, 14, 10, 0, tzinfo=UTC)
    current_time = export_time

    payload = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )

    policy_budget_snapshot = _mapping(payload["policy_budget_snapshot"])
    provenance = _mapping(payload["provenance"])
    latest_event = _mapping(provenance["latest_event"])
    terminal_event = _mapping(provenance["terminal_event"])
    violations = policy_budget_snapshot["approval_policy_violations"]
    assert isinstance(violations, list)
    assert any(isinstance(item, str) and "approve nothing stale" in item for item in violations)
    assert policy_budget_snapshot["evaluated_at"] == export_time.isoformat()
    assert latest_event["action"] == "filled"
    assert latest_event["timestamp"] != policy_budget_snapshot["evaluated_at"]
    assert terminal_event["timestamp"] != policy_budget_snapshot["evaluated_at"]


def test_export_reports_resolved_active_autonomy_mode(service: TradeIdeaService) -> None:
    """policy_budget_snapshot carries the mode the violations were evaluated
    against (the resolved active level), while decision_metadata preserves the
    mode the idea claimed at proposal time."""
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    service.set_autonomy_mode(
        AutonomyMode.RESEARCH_ONLY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Pause approvals during incident review",
    )

    payload = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="manual",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )

    policy_budget_snapshot = _mapping(payload["policy_budget_snapshot"])
    decision_metadata = _mapping(payload["decision_metadata"])
    assert policy_budget_snapshot["autonomy_mode"] == "research_only"
    assert policy_budget_snapshot["autonomy_mode_source"] == "autonomy_state_log"
    assert decision_metadata["autonomy_mode"] == "human_approved_execution"
    violations = policy_budget_snapshot["approval_policy_violations"]
    assert isinstance(violations, list)
    assert any(isinstance(item, str) and "research_only" in item for item in violations)
