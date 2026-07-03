"""CLI tests for ``ideas cycle``: one offline turn of the Stage-1 paper loop.

Runner contracts are pinned in
tests/unit/gpt_trader/features/idea_execution/test_cycle.py; these tests cover
the adapter: snapshot-file injection, proposer selection, the human-approval
seam between two turns, and envelope shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas import (
    MarketSnapshot,
    SymbolSeries,
    market_snapshot_to_payload,
)
from tests.unit.gpt_trader.cli.commands.conftest import attest_ideas_root

_AS_OF = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _crossover_snapshot_payload() -> dict[str, Any]:
    closes = [Decimal("100")] * 57 + [Decimal("120"), Decimal("125"), Decimal("130")]
    volumes = [Decimal("10")] * 59 + [Decimal("100")]
    start = _AS_OF - timedelta(hours=len(closes))
    series = SymbolSeries(
        symbol="BTC-USD",
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
        MarketSnapshot(as_of=_AS_OF, source="test:fixture", series=(series,))
    )


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(_crossover_snapshot_payload()), encoding="utf-8")
    return path


def test_cycle_proposes_then_executes_after_human_approval(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, snapshot_path: Path
) -> None:
    root = tmp_path / "ideas"
    attest_ideas_root(root)

    exit_code, first = _run_json(
        capsys,
        ["ideas", "cycle", "--snapshot", str(snapshot_path), *_root_args(root)],
    )
    assert exit_code == 0
    assert first["success"] is True
    baseline_turn, regime_turn = first["data"]["proposers"]
    assert baseline_turn["proposal_count"] == 1
    (decision_id,) = baseline_turn["proposed_decision_ids"]
    # The second proposer sees the instrument already queued and defers.
    assert regime_turn["proposal_count"] == 0
    assert regime_turn["skipped_open_instruments"][0]["existing_decision_id"] == decision_id
    assert first["data"]["execution"]["executed"] == []

    exit_code, approved = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            decision_id,
            *_root_args(root),
            "--actor",
            "human-reviewer",
            "--reason",
            "cycle CLI test approval",
        ],
    )
    assert exit_code == 0 and approved["success"] is True

    exit_code, second = _run_json(
        capsys,
        ["ideas", "cycle", "--snapshot", str(snapshot_path), *_root_args(root)],
    )
    assert exit_code == 0
    (executed,) = second["data"]["execution"]["executed"]
    assert executed["decision_id"] == decision_id
    assert executed["client_order_id"] == decision_id
    assert executed["fill_price"] == "130"
    assert executed["final_state"] == "filled"

    manifest_path = root / "cycle" / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text().splitlines() if line]
    assert len(rows) == 2
    assert all(row["outcome"] == "completed" for row in rows)


def test_cycle_proposer_selection_is_configuration(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, snapshot_path: Path
) -> None:
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "cycle",
            "--snapshot",
            str(snapshot_path),
            "--proposer",
            "baseline",
            *_root_args(root),
        ],
    )
    assert exit_code == 0
    (only_turn,) = response["data"]["proposers"]
    assert only_turn["proposer_id"].startswith("baseline")


def _strategy_crossover_snapshot_payload() -> dict[str, Any]:
    """Golden cross sized for the strategy's 5/20 MAs and 3-bar crossover lookback."""
    closes = [Decimal("100")] * 28 + [Decimal("102"), Decimal("104")]
    start = _AS_OF - timedelta(hours=len(closes))
    series = SymbolSeries(
        symbol="BTC-USD",
        granularity="ONE_HOUR",
        candles=tuple(
            Candle(
                ts=start + timedelta(hours=index),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("1000"),
            )
            for index, close in enumerate(closes)
        ),
    )
    return market_snapshot_to_payload(
        MarketSnapshot(as_of=_AS_OF, source="test:fixture", series=(series,))
    )


def test_cycle_strategy_backed_proposer_is_opt_in_configuration(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    strategy_snapshot_path = tmp_path / "strategy-snapshot.json"
    strategy_snapshot_path.write_text(
        json.dumps(_strategy_crossover_snapshot_payload()), encoding="utf-8"
    )
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "cycle",
            "--snapshot",
            str(strategy_snapshot_path),
            "--proposer",
            "strategy-baseline-spot",
            *_root_args(root),
        ],
    )
    assert exit_code == 0
    (only_turn,) = response["data"]["proposers"]
    assert only_turn["proposer_id"] == "snapshot-strategy-baseline-spot"
    assert only_turn["proposal_count"] == 1


def test_cycle_from_coinbase_requires_market_parameters(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "ideas"
    exit_code, response = _run_json(
        capsys,
        ["ideas", "cycle", "--from-coinbase", *_root_args(root)],
    )
    assert exit_code != 0
    assert response["success"] is False
    message = response["errors"][0]["message"]
    assert "--symbols" in message and "--granularity" in message and "--lookback" in message
