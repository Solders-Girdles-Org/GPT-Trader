"""Scorecard max-drawdown-from-peak gate: windowed read from the equity ledger (#1192).

Split from test_scorecard.py to respect the test-hygiene module-size cap; the
shared trail-building helpers are imported from that module.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from tests.unit.gpt_trader.features.trade_ideas.conftest import attest_account_equity
from tests.unit.gpt_trader.features.trade_ideas.test_scorecard import (
    _START,
    _Clock,
    _close_with_pnl,
    _service,
)

from gpt_trader.features.trade_ideas import TradeIdeaService
from gpt_trader.features.trade_ideas.scorecard import build_stage_promotion_scorecard


def _configure_drawdown_appetite(service: TradeIdeaService, limit: str) -> None:
    from dataclasses import replace

    from gpt_trader.features.trade_ideas import ActorType

    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            max_drawdown_from_peak_pct=Decimal(limit),
            reason="Configure the drawdown-from-peak appetite",
        ),
        ActorType.HUMAN,
        "rj",
    )


def test_drawdown_gate_not_measurable_without_attested_ledger(tmp_path: Path) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)

    payload = build_stage_promotion_scorecard(service, now=_START + timedelta(days=70))

    gate = payload["gates"]["max_drawdown_from_peak"]
    assert gate["status"] == "not_yet_measurable"
    assert "no attested-equity ledger points" in gate["detail"]


def test_drawdown_gate_reports_measurement_even_without_a_limit(tmp_path: Path) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)
    attest_account_equity(service)  # 20000
    _close_with_pnl(
        service,
        clock,
        "trade-loss-001",
        proposer_actor_id="strategy-x",
        realized_amount=Decimal("-200"),
        closed_at=_START + timedelta(days=5),
    )

    payload = build_stage_promotion_scorecard(service, now=_START + timedelta(days=30))

    gate = payload["gates"]["max_drawdown_from_peak"]
    assert gate["status"] == "not_yet_measurable"
    assert gate["measured"]["max_drawdown_from_peak_pct"] == "1.00"  # 200/20000
    assert "no max_drawdown_from_peak_pct configured" in gate["detail"]


def test_drawdown_gate_scores_windowed_max_against_the_budget_limit(tmp_path: Path) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)
    attest_account_equity(service)  # 20000
    _configure_drawdown_appetite(service, "2")
    # Trough of 600 below the 20200 peak: max drawdown-from-peak ~2.97%.
    _close_with_pnl(
        service,
        clock,
        "trade-win-001",
        proposer_actor_id="strategy-x",
        realized_amount=Decimal("200"),
        closed_at=_START + timedelta(days=2),
    )
    _close_with_pnl(
        service,
        clock,
        "trade-loss-001",
        proposer_actor_id="strategy-x",
        realized_amount=Decimal("-600"),
        closed_at=_START + timedelta(days=5),
    )
    _close_with_pnl(
        service,
        clock,
        "trade-win-002",
        proposer_actor_id="strategy-x",
        realized_amount=Decimal("500"),
        closed_at=_START + timedelta(days=10),
    )

    payload = build_stage_promotion_scorecard(service, now=_START + timedelta(days=30))

    gate = payload["gates"]["max_drawdown_from_peak"]
    assert gate["status"] == "fail"
    assert gate["measured"]["max_drawdown_from_peak_pct"] == "2.97"
    assert gate["measured"]["max_drawdown_from_peak_limit_pct"] == "2"

    _configure_drawdown_appetite(service, "10")
    relaxed = build_stage_promotion_scorecard(service, now=_START + timedelta(days=30))
    assert relaxed["gates"]["max_drawdown_from_peak"]["status"] == "pass"
