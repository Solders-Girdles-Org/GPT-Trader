#!/usr/bin/env python3
"""Offline end-to-end smoke for the Stage 0/1 trade-idea rails.

Drives the real ``gpt-trader ideas`` CLI through the full record lifecycle in
a scratch ideas root: propose -> (approval blocked without attested equity) ->
budget attest -> approve -> export-ticket -> mark-submitted -> mark-filled ->
closeout -> report -> audit verify.

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

    report = _run_ideas_cli(ideas_root, "report")
    report_data = report["data"]
    _assert(
        int(report_data["row_count"]) == 1,
        "report",
        f"expected exactly 1 idea, got row_count={report_data.get('row_count')}",
    )
    closeouts = report_data.get("closeouts", {})
    _assert(
        int(closeouts.get("missing_closeout_count", -1)) == 0,
        "report",
        f"expected full closeout coverage, got {closeouts}",
    )
    _step("report -> 1 idea, full closeout coverage")

    verify = _run_ideas_cli(ideas_root, "audit", "verify")
    _assert(
        int(verify["data"].get("event_count", 0)) >= 4,
        "audit verify",
        f"expected >=4 chained events, got {verify['data']}",
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
    print("✓ stage1 rails smoke OK: proposed -> approved -> filled -> closed, audit chain intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
