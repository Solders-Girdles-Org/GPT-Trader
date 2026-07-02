#!/usr/bin/env python3
"""Offline end-to-end smoke for the Stage 0/1 trade-idea rails.

Drives the real ``gpt-trader ideas`` CLI through the execution legs in a
scratch ideas root. Manual leg: propose -> (approval blocked without attested
equity) -> budget attest -> approve -> export-ticket -> mark-submitted ->
mark-filled -> closeout. Machine leg: propose -> approve -> execute-paper
(deterministic paper broker, no attestation) -> refused re-execution ->
closeout. Cycle leg (issue #1150): `ideas cycle` over a fixture snapshot
proposes; a human approves between turns; the next turn paper-executes at the
snapshot mark and each turn appends a manifest row. Then: report over all
legs -> audit verify.

This is the "does the project still string together?" gate: unit tests cover
the parts, this covers the operator-visible loop. It needs no network, broker,
credentials, or pre-existing state, and finishes in well under a minute.

Usage:
    uv run python scripts/ops/stage1_rails_smoke.py [--keep-root]

Exit code 0 iff every step succeeds (including the expected approval block).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SMOKE_ACTOR_AI = "stage1-smoke-proposer"
SMOKE_ACTOR_HUMAN = "stage1-smoke-operator"
SMOKE_VENUE = "manual"
SMOKE_EXTERNAL_ORDER_ID = "SMOKE-EXT-001"


class SmokeStepError(RuntimeError):
    """Raised when a lifecycle step does not behave as asserted."""


def _build_idea_payload(decision_id: str) -> dict[str, Any]:
    """Build one fully-populated, eligible spot trade idea as CLI JSON input."""
    from decimal import Decimal

    from gpt_trader.features.trade_ideas import (
        AutonomyMode,
        Confidence,
        ConfidenceLabel,
        EntryZone,
        MaxLoss,
        ProductType,
        SizingRecommendation,
        TimeHorizon,
        TradeDirection,
        TradeIdea,
    )

    idea = TradeIdea(
        decision_id=decision_id,
        autonomy_mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
        thesis="Stage 1 rails smoke: fixed eligible record exercising the lifecycle",
        instrument="BTC-USD",
        product_type=ProductType.SPOT,
        direction=TradeDirection.LONG,
        entry_zone=EntryZone(lower=Decimal("60000"), upper=Decimal("61500")),
        invalidation="Daily close below 58000",
        target_exit="Take profit at 67000 or exit after 10 trading days",
        max_loss=MaxLoss(
            amount=Decimal("250"),
            percent_of_account=Decimal("1.5"),
            assumptions=("Fill at zone midpoint",),
        ),
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=Decimal("6075"),
            rationale="Fixed smoke sizing; not a live recommendation",
        ),
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
        data_used=("smoke:fixture:no-market-data",),
        confidence=Confidence(
            label=ConfidenceLabel.MEDIUM,
            rationale="Smoke fixture confidence",
        ),
        failure_mode="Not applicable: smoke fixture never reaches a broker",
        do_not_trade_if=("This is a smoke-test record",),
    )
    return idea.to_dict()


def _build_cycle_snapshot_payload(symbol: str) -> dict[str, Any]:
    """Synthesize a snapshot whose 10/50 MA golden cross lands in the last 3 bars."""
    from decimal import Decimal

    from gpt_trader.core import Candle
    from gpt_trader.features.trade_ideas import (
        MarketSnapshot,
        SymbolSeries,
        market_snapshot_to_payload,
    )

    as_of = datetime.now(UTC)
    closes = [Decimal("100")] * 57 + [Decimal("120"), Decimal("125"), Decimal("130")]
    volumes = [Decimal("10")] * 59 + [Decimal("100")]
    start = as_of - timedelta(hours=len(closes))
    series = SymbolSeries(
        symbol=symbol,
        granularity="ONE_HOUR",
        candles=tuple(
            Candle(
                ts=start + timedelta(hours=index),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=volume,
            )
            for index, (close, volume) in enumerate(zip(closes, volumes, strict=True))
        ),
    )
    return market_snapshot_to_payload(
        MarketSnapshot(as_of=as_of, source="smoke:fixture:cycle", series=(series,))
    )


def _run_ideas_cli(
    ideas_root: Path,
    *args: str,
    expect_success: bool = True,
) -> dict[str, Any]:
    """Run one ``gpt-trader ideas`` command and return its parsed JSON envelope."""
    command = [
        sys.executable,
        "-m",
        "gpt_trader.cli",
        "ideas",
        *args,
        "--ideas-root",
        str(ideas_root),
        "--format",
        "json",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    succeeded = completed.returncode == 0
    if succeeded != expect_success:
        expectation = "succeed" if expect_success else "fail"
        raise SmokeStepError(
            f"expected `ideas {args[0]}` to {expectation} "
            f"(exit={completed.returncode})\nstdout: {completed.stdout.strip()}"
            f"\nstderr: {completed.stderr.strip()}"
        )
    try:
        envelope: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeStepError(
            f"`ideas {args[0]}` did not emit a JSON envelope: {exc}"
            f"\nstdout: {completed.stdout.strip()}"
        ) from exc
    return envelope


def _assert(condition: bool, step: str, detail: str) -> None:
    if not condition:
        raise SmokeStepError(f"{step}: {detail}")


def _step(label: str) -> None:
    print(f"✓ {label}")


def run_smoke(ideas_root: Path) -> None:
    decision_id = f"trade-{datetime.now(UTC):%Y%m%d}-smoke-001"

    idea_path = ideas_root / "smoke_idea.json"
    idea_path.write_text(json.dumps(_build_idea_payload(decision_id)))

    proposed = _run_ideas_cli(
        ideas_root,
        "propose",
        "--file",
        str(idea_path),
        "--actor",
        SMOKE_ACTOR_AI,
        "--actor-type",
        "ai",
        "--reason",
        "stage1 rails smoke",
    )
    _assert(
        proposed["data"]["state"] == "proposed",
        "propose",
        f"unexpected state {proposed['data']}",
    )
    _step("propose (ai actor) -> proposed")

    # The budget gate must refuse approval while account equity is unattested:
    # a candidate idea can never supply its own notional denominator.
    _run_ideas_cli(
        ideas_root,
        "approve",
        decision_id,
        "--actor",
        SMOKE_ACTOR_HUMAN,
        "--reason",
        "smoke approval before equity attestation (must be blocked)",
        expect_success=False,
    )
    _step("approve without attested equity -> blocked by budget gate")

    budget = _run_ideas_cli(
        ideas_root,
        "budget",
        "set",
        "--account-equity",
        "25000",
        "--actor",
        SMOKE_ACTOR_HUMAN,
        "--reason",
        "stage1 rails smoke: attest scratch equity",
    )
    _assert(
        int(budget["data"]["version"]) >= 2,
        "budget set",
        f"expected a new budget version, got {budget['data']}",
    )
    _step("budget set (human attests account equity)")

    approved = _run_ideas_cli(
        ideas_root,
        "approve",
        decision_id,
        "--actor",
        SMOKE_ACTOR_HUMAN,
        "--reason",
        "stage1 rails smoke approval",
    )
    _assert(
        approved["data"]["state"] == "approved",
        "approve",
        f"unexpected state {approved['data']}",
    )
    _step("approve (human actor) -> approved")

    # export-ticket emits the raw ticket artifact, not the CliResponse envelope.
    ticket = _run_ideas_cli(
        ideas_root,
        "export-ticket",
        "--decision-id",
        decision_id,
        "--venue",
        SMOKE_VENUE,
    )
    _assert(
        bool(ticket.get("ticket_hash")) and bool(ticket.get("record_hash")),
        "export-ticket",
        f"missing ticket_hash/record_hash in keys {sorted(ticket)}",
    )
    _step("export-ticket -> deterministic ticket with content hash")

    submitted = _run_ideas_cli(
        ideas_root,
        "mark-submitted",
        decision_id,
        "--venue",
        SMOKE_VENUE,
        "--external-order-id",
        SMOKE_EXTERNAL_ORDER_ID,
        "--actor",
        SMOKE_ACTOR_HUMAN,
        "--actor-type",
        "human",
        "--reason",
        "stage1 rails smoke submission attestation",
    )
    _assert(
        submitted["data"]["state"] == "submitted",
        "mark-submitted",
        f"unexpected state {submitted['data']}",
    )
    _step("mark-submitted (attestation) -> submitted")

    filled = _run_ideas_cli(
        ideas_root,
        "mark-filled",
        decision_id,
        "--venue",
        SMOKE_VENUE,
        "--external-order-id",
        SMOKE_EXTERNAL_ORDER_ID,
        "--actor",
        SMOKE_ACTOR_HUMAN,
        "--reason",
        "stage1 rails smoke fill attestation",
    )
    _assert(
        filled["data"]["state"] == "filled",
        "mark-filled",
        f"unexpected state {filled['data']}",
    )
    _step("mark-filled (attestation) -> filled")

    _run_ideas_cli(
        ideas_root,
        "closeout",
        "record",
        decision_id,
        "--resolution",
        "thesis_target",
        "--realized-profit-loss-amount",
        "120",
        "--realized-profit-loss-percent",
        "0.48",
        "--evidence",
        "stage1 rails smoke exit",
        "--actor",
        SMOKE_ACTOR_HUMAN,
    )
    _step("closeout record -> attribution captured")

    # --- Machine leg: approved -> paper fill with no manual attestation. ---
    machine_decision_id = f"trade-{datetime.now(UTC):%Y%m%d}-smoke-002"
    machine_idea_path = ideas_root / "smoke_idea_machine.json"
    machine_idea_path.write_text(json.dumps(_build_idea_payload(machine_decision_id)))

    machine_proposed = _run_ideas_cli(
        ideas_root,
        "propose",
        "--file",
        str(machine_idea_path),
        "--actor",
        SMOKE_ACTOR_AI,
        "--actor-type",
        "ai",
        "--reason",
        "stage1 rails smoke machine leg",
    )
    _assert(
        machine_proposed["data"]["state"] == "proposed",
        "propose (machine leg)",
        f"unexpected state {machine_proposed['data']}",
    )
    machine_approved = _run_ideas_cli(
        ideas_root,
        "approve",
        machine_decision_id,
        "--actor",
        SMOKE_ACTOR_HUMAN,
        "--reason",
        "stage1 rails smoke machine-leg approval",
    )
    _assert(
        machine_approved["data"]["state"] == "approved",
        "approve (machine leg)",
        f"unexpected state {machine_approved['data']}",
    )
    _step("machine leg: propose (ai actor) -> approve (human actor)")

    # Fill at the entry-zone midpoint so the simulated execution is priced
    # like the idea, not at the broker's arbitrary default mark.
    executed = _run_ideas_cli(
        ideas_root,
        "execute-paper",
        machine_decision_id,
        "--mark",
        "60750",
    )
    executed_data = executed["data"]
    _assert(
        executed_data["final_state"] == "filled",
        "execute-paper",
        f"expected filled, got {executed_data}",
    )
    _assert(
        executed_data["client_order_id"] == machine_decision_id,
        "execute-paper",
        f"client_order_id must be the decision id, got {executed_data}",
    )
    _assert(
        executed_data["reconciliation"]["recorded_fill"] is True,
        "execute-paper",
        f"fill was not recorded through the reconciler, got {executed_data}",
    )
    _step("execute-paper (system actor) -> submitted and filled, no attestation")

    # The lane must refuse to execute the same idea twice.
    _run_ideas_cli(
        ideas_root,
        "execute-paper",
        machine_decision_id,
        expect_success=False,
    )
    _step("execute-paper again -> refused (no double execution)")

    _run_ideas_cli(
        ideas_root,
        "closeout",
        "record",
        machine_decision_id,
        "--resolution",
        "thesis_target",
        "--realized-profit-loss-amount",
        "35",
        "--realized-profit-loss-percent",
        "0.14",
        "--evidence",
        "stage1 rails smoke machine-leg exit",
        "--actor",
        SMOKE_ACTOR_HUMAN,
    )
    _step("closeout record (machine leg) -> attribution captured")

    # --- Cycle leg: one scheduled turn proposes; a human approves between
    # turns; the next turn paper-executes at the snapshot mark. ---
    cycle_snapshot_path = ideas_root / "smoke_cycle_snapshot.json"
    cycle_snapshot_path.write_text(json.dumps(_build_cycle_snapshot_payload("SOL-USD")))

    cycle_first = _run_ideas_cli(
        ideas_root,
        "cycle",
        "--snapshot",
        str(cycle_snapshot_path),
    )
    cycle_first_data = cycle_first["data"]
    baseline_turn = cycle_first_data["proposers"][0]
    _assert(
        int(baseline_turn["proposal_count"]) == 1,
        "cycle (first turn)",
        f"expected 1 baseline proposal, got {cycle_first_data['proposers']}",
    )
    cycle_decision_id = baseline_turn["proposed_decision_ids"][0]
    _assert(
        cycle_first_data["execution"]["executed"] == [],
        "cycle (first turn)",
        f"nothing was approved yet, but got executions {cycle_first_data['execution']}",
    )
    _step("cycle turn 1 -> proposed from fixture snapshot, nothing executed")

    cycle_approved = _run_ideas_cli(
        ideas_root,
        "approve",
        cycle_decision_id,
        "--actor",
        SMOKE_ACTOR_HUMAN,
        "--reason",
        "stage1 rails smoke cycle-leg approval",
    )
    _assert(
        cycle_approved["data"]["state"] == "approved",
        "approve (cycle leg)",
        f"unexpected state {cycle_approved['data']}",
    )
    _step("cycle leg: approve (human actor) between turns")

    cycle_second = _run_ideas_cli(
        ideas_root,
        "cycle",
        "--snapshot",
        str(cycle_snapshot_path),
    )
    cycle_executed = cycle_second["data"]["execution"]["executed"]
    _assert(
        len(cycle_executed) == 1 and cycle_executed[0]["decision_id"] == cycle_decision_id,
        "cycle (second turn)",
        f"expected the approved idea to execute, got {cycle_second['data']['execution']}",
    )
    _assert(
        cycle_executed[0]["fill_price"] == "130",
        "cycle (second turn)",
        f"expected fill at the snapshot mark 130, got {cycle_executed[0]}",
    )
    _step("cycle turn 2 -> approved idea paper-executed at the snapshot mark")

    manifest_path = ideas_root / "cycle" / "manifest.jsonl"
    manifest_rows = [
        json.loads(line) for line in manifest_path.read_text().splitlines() if line.strip()
    ]
    _assert(
        len(manifest_rows) == 2 and all(row["outcome"] == "completed" for row in manifest_rows),
        "cycle manifest",
        f"expected 2 completed manifest rows, got {manifest_rows}",
    )
    _step("cycle manifest -> exactly one completed row per turn")

    _run_ideas_cli(
        ideas_root,
        "closeout",
        "record",
        cycle_decision_id,
        "--resolution",
        "thesis_target",
        "--realized-profit-loss-amount",
        "12",
        "--realized-profit-loss-percent",
        "0.05",
        "--evidence",
        "stage1 rails smoke cycle-leg exit",
        "--actor",
        SMOKE_ACTOR_HUMAN,
    )
    _step("closeout record (cycle leg) -> attribution captured")

    report = _run_ideas_cli(ideas_root, "report")
    report_data = report["data"]
    _assert(
        int(report_data["row_count"]) == 3,
        "report",
        f"expected exactly 3 ideas, got row_count={report_data.get('row_count')}",
    )
    closeouts = report_data.get("closeouts", {})
    _assert(
        int(closeouts.get("missing_closeout_count", -1)) == 0,
        "report",
        f"expected full closeout coverage, got {closeouts}",
    )
    _step("report -> 3 ideas, full closeout coverage")

    verify = _run_ideas_cli(ideas_root, "audit", "verify")
    _assert(
        int(verify["data"].get("event_count", 0)) >= 12,
        "audit verify",
        f"expected >=12 chained events, got {verify['data']}",
    )
    _step(f"audit verify -> chain intact ({verify['data']['event_count']} events)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-root",
        action="store_true",
        help="Keep the scratch ideas root for inspection instead of deleting it",
    )
    args = parser.parse_args()

    if args.keep_root:
        ideas_root = Path(tempfile.mkdtemp(prefix="stage1-rails-smoke-"))
        print(f"scratch ideas root: {ideas_root}")
        return _execute(ideas_root)
    with tempfile.TemporaryDirectory(prefix="stage1-rails-smoke-") as tmp:
        return _execute(Path(tmp))


def _execute(ideas_root: Path) -> int:
    try:
        run_smoke(ideas_root)
    except SmokeStepError as exc:
        print(f"✗ stage1 rails smoke FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        "✓ stage1 rails smoke OK: manual, machine, and cycle legs "
        "proposed -> approved -> filled -> closed, audit chain intact"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
