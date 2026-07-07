"""Structured exit plan on the trade-idea record (M4 #1218a).

A filled idea needs machine-readable stop/target levels to resolve its outcome
against later marks (invalidation gets the levels only into free text). The plan
is optional and must serialize hash-compatibly: a record without it must hash
exactly as before, or every audit-integrity check on the existing trail breaks.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.trade_ideas import ExitPlan


def test_exit_plan_roundtrips_through_dict() -> None:
    plan = ExitPlan(stop=Decimal("58000"), target=Decimal("67000"))
    assert ExitPlan.from_dict(plan.to_dict()) == plan


def test_exit_plan_rejects_non_finite() -> None:
    with pytest.raises(ValueError):
        ExitPlan(stop=Decimal("NaN"), target=Decimal("67000"))


def test_trade_idea_omits_exit_plan_when_absent() -> None:
    idea = build_trade_idea()
    assert idea.exit_plan is None
    # Omitted (not null) so a legacy record's canonical form is byte-identical.
    assert "exit_plan" not in idea.to_dict()


def test_absent_exit_plan_preserves_record_hash() -> None:
    """A record with no exit plan must hash exactly as it did before the field."""
    idea = build_trade_idea()
    legacy_payload = idea.to_dict()  # no "exit_plan" key
    from gpt_trader.features.trade_ideas import TradeIdea

    reloaded = TradeIdea.from_dict(legacy_payload)
    assert reloaded.exit_plan is None
    assert reloaded.record_hash() == idea.record_hash()


def test_present_exit_plan_roundtrips_and_changes_hash() -> None:
    base = build_trade_idea()
    with_plan = replace(base, exit_plan=ExitPlan(stop=Decimal("58000"), target=Decimal("67000")))

    payload = with_plan.to_dict()
    assert payload["exit_plan"] == {"stop": "58000", "target": "67000"}

    from gpt_trader.features.trade_ideas import TradeIdea

    assert TradeIdea.from_dict(payload) == with_plan
    # A populated plan is part of the pinned content, so the hash must move.
    assert with_plan.record_hash() != base.record_hash()
