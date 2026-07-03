"""CLI tests for ``ideas propose-strategy``: live-trade strategies as proposers.

The proposer/adapter contracts are pinned in
tests/unit/gpt_trader/features/strategy_tools/; these tests cover the CLI
composition root: strategy selection, budget-backed sizing injection,
price-precision surfacing for sub-cent symbols, and determinism through the
audited service.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    RiskBudget,
    TradeIdeaService,
)

AS_OF = datetime(2035, 6, 12, 0, 0, tzinfo=UTC)
# Flat closes then a two-bar rise: the 5-bar MA crosses above the 20-bar MA
# within the strategy's crossover lookback and the trend turns bullish, so the
# baseline entry gate clears (crossover 0.4 + trend 0.3 >= min_confidence 0.5).
GOLDEN_CROSS = ["100"] * 28 + ["102", "104"]
FLAT = ["100"] * 30
SUB_CENT_GOLDEN_CROSS = ["0.004"] * 28 + ["0.0041", "0.0042"]
# Flat closes then a sharp final-bar dip: Z-Score over the 20-candle window
# drops far below the -2.0 entry threshold (mean reversion long).
MEAN_REVERSION_DIP = ["100"] * 29 + ["96"]
# Ultra-quiet damped oscillation, then the same dip: strictly shrinking moves
# keep the regime detector's classification stable so the first regime
# confirms at candle 54 (long-EMA 50 + min-regime-ticks 5 - 1) as
# SIDEWAYS_QUIET, routing the final-bar dip to the mean-reversion delegate.
REGIME_SWITCHER_DIP = [
    f"{100 + (1 if i % 2 == 0 else -1) * 0.05 * (0.995 ** i):.4f}" for i in range(59)
] + ["96"]


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _snapshot_payload(closes: list[str] = GOLDEN_CROSS) -> dict[str, Any]:
    candles = [
        {
            "ts": (AS_OF - timedelta(days=len(closes) - index)).isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": "1000",
        }
        for index, close in enumerate(closes)
    ]
    return {
        "as_of": AS_OF.isoformat(),
        "source": "local-fixture:coinbase-candles",
        "series": [{"symbol": "BTC-USD", "granularity": "1d", "candles": candles}],
    }


def _write_snapshot(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _propose_strategy(
    capsys: pytest.CaptureFixture[str],
    root: Path,
    snapshot_path: Path,
    *,
    strategy: str = "baseline-spot",
    extra: list[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    return _run_json(
        capsys,
        [
            "ideas",
            "propose-strategy",
            "--ideas-root",
            str(root),
            "--format",
            "json",
            "--snapshot",
            str(snapshot_path),
            "--strategy",
            strategy,
            *(extra or []),
        ],
    )


def test_propose_strategy_persists_executable_proposal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(tmp_path / "snapshot.json", _snapshot_payload())

    exit_code, response = _propose_strategy(capsys, root, snapshot_path)

    assert exit_code == 0
    assert response["success"] is True
    assert response["command"] == "ideas propose-strategy"
    assert response["data"]["proposer_id"] == "snapshot-strategy-baseline-spot"
    assert response["data"]["proposal_count"] == 1
    proposal = response["data"]["proposed"][0]
    assert proposal["decision_id"].startswith("trade-20350612-baseline-spot-btc-usd-")
    assert proposal["state"] == "proposed"
    latest = json.loads(
        (root / "records" / proposal["decision_id"] / "latest.json").read_text(encoding="utf-8")
    )
    # Executor admission requires real sizing, not the advisory default.
    assert latest["product_type"] == "spot"
    assert latest["direction"] == "long"
    assert latest["sizing_recommendation"]["quantity"] is not None
    assert latest["sizing_recommendation"]["notional"] is not None
    assert latest["max_loss"]["amount"] is not None
    # Sized proposals carry a notional, so on an unattested root the preview
    # surfaces the fail-closed equity gate instead of a missing-notional gap.
    assert proposal["approval_preview"]["violations"] == [
        "account_equity_snapshot is required to verify max_open_notional_pct budget exposure"
    ]
    event = json.loads((root / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert event["actor_type"] == "ai"
    assert event["actor_id"] == "snapshot-strategy-baseline-spot"
    assert "proposer_id=snapshot-strategy-baseline-spot" in event["evidence"]


def test_propose_strategy_baseline_perps_choice_emits_spot_ideas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(tmp_path / "snapshot.json", _snapshot_payload())

    exit_code, response = _propose_strategy(capsys, root, snapshot_path, strategy="baseline-perps")

    assert exit_code == 0
    assert response["data"]["proposer_id"] == "snapshot-strategy-baseline-perps"
    assert response["data"]["proposal_count"] == 1
    proposal = response["data"]["proposed"][0]
    latest = json.loads(
        (root / "records" / proposal["decision_id"] / "latest.json").read_text(encoding="utf-8")
    )
    assert latest["product_type"] == "spot"


def test_propose_strategy_mean_reversion_choice_buys_the_dip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(tmp_path / "dip.json", _snapshot_payload(MEAN_REVERSION_DIP))

    exit_code, response = _propose_strategy(capsys, root, snapshot_path, strategy="mean-reversion")

    assert exit_code == 0
    assert response["data"]["proposer_id"] == "snapshot-strategy-mean-reversion"
    assert response["data"]["proposal_count"] == 1
    proposal = response["data"]["proposed"][0]
    assert proposal["decision_id"].startswith("trade-20350612-mean-reversion-btc-usd-")
    latest = json.loads(
        (root / "records" / proposal["decision_id"] / "latest.json").read_text(encoding="utf-8")
    )
    assert latest["product_type"] == "spot"
    assert latest["direction"] == "long"
    assert latest["sizing_recommendation"]["quantity"] is not None
    assert latest["max_loss"]["amount"] is not None


def test_propose_strategy_regime_switcher_choice_buys_the_sideways_dip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(
        tmp_path / "sideways-dip.json", _snapshot_payload(REGIME_SWITCHER_DIP)
    )

    exit_code, response = _propose_strategy(capsys, root, snapshot_path, strategy="regime-switcher")

    assert exit_code == 0
    assert response["data"]["proposer_id"] == "snapshot-strategy-regime-switcher"
    assert response["data"]["proposal_count"] == 1
    proposal = response["data"]["proposed"][0]
    latest = json.loads(
        (root / "records" / proposal["decision_id"] / "latest.json").read_text(encoding="utf-8")
    )
    assert latest["product_type"] == "spot"
    assert latest["direction"] == "long"
    assert latest["sizing_recommendation"]["quantity"] is not None


def test_propose_strategy_regime_switcher_holds_below_detector_warmup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    # The dip is present, but only 30 candles precede it: the regime detector
    # cannot confirm a regime, so the switcher holds and nothing is proposed.
    snapshot_path = _write_snapshot(
        tmp_path / "short-dip.json", _snapshot_payload(MEAN_REVERSION_DIP)
    )

    exit_code, response = _propose_strategy(capsys, root, snapshot_path, strategy="regime-switcher")

    assert exit_code == 0
    assert response["data"]["proposal_count"] == 0
    assert response["metadata"]["was_noop"] is True


def test_propose_strategy_sizes_with_attested_account_equity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    service = TradeIdeaService(root)
    service.update_budget(
        RiskBudget.from_dict(
            {
                **DEFAULT_RISK_BUDGET.to_dict(),
                "version": 2,
                "account_equity": "1000",
            }
        ),
        actor_type=ActorType.HUMAN,
        actor_id="rj",
    )
    snapshot_path = _write_snapshot(tmp_path / "snapshot.json", _snapshot_payload())

    exit_code, response = _propose_strategy(capsys, root, snapshot_path)

    assert exit_code == 0
    proposal = response["data"]["proposed"][0]
    latest = json.loads(
        (root / "records" / proposal["decision_id"] / "latest.json").read_text(encoding="utf-8")
    )
    # The composition root must inject the budget-backed bridge: sizing is
    # denominated by the attested equity the approval gate uses, not the
    # bridge's offline default.
    sizing_inputs = next(item for item in latest["data_used"] if item.startswith("sizing:"))
    assert "equity=1000" in sizing_inputs
    assert proposal["approval_preview"]["violations"] == []


def test_propose_strategy_no_signal_is_noop_and_reads_no_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(tmp_path / "flat.json", _snapshot_payload(FLAT))

    exit_code, response = _propose_strategy(capsys, root, snapshot_path)

    assert exit_code == 0
    assert response["data"]["proposal_count"] == 0
    assert response["metadata"]["was_noop"] is True
    assert not (root / "records").exists()
    assert not (root / "audit.jsonl").exists()
    # The budget must not be read or seeded when nothing needed sizing.
    assert not (root / "risk_budget.jsonl").exists()


def test_propose_strategy_duplicate_rerun_fails_without_extra_audit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(tmp_path / "snapshot.json", _snapshot_payload())
    first_exit_code, _ = _propose_strategy(capsys, root, snapshot_path)
    assert first_exit_code == 0
    original_audit = (root / "audit.jsonl").read_text(encoding="utf-8")

    exit_code, response = _propose_strategy(capsys, root, snapshot_path)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.VALIDATION_ERROR.value
    assert response["errors"][0]["details"]["field"] == "decision_id"
    assert (root / "audit.jsonl").read_text(encoding="utf-8") == original_audit


def test_propose_strategy_sub_cent_mark_fails_closed_at_default_precision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(
        tmp_path / "sub-cent.json", _snapshot_payload(SUB_CENT_GOLDEN_CROSS)
    )

    exit_code, response = _propose_strategy(capsys, root, snapshot_path)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.VALIDATION_ERROR.value
    assert "price_precision" in response["errors"][0]["message"]
    assert not (root / "records").exists()


def test_propose_strategy_finer_price_precision_unlocks_sub_cent_symbols(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_snapshot(
        tmp_path / "sub-cent.json", _snapshot_payload(SUB_CENT_GOLDEN_CROSS)
    )

    exit_code, response = _propose_strategy(
        capsys,
        root,
        snapshot_path,
        extra=["--price-precision", "0.000001"],
    )

    assert exit_code == 0
    assert response["data"]["proposal_count"] == 1
    proposal = response["data"]["proposed"][0]
    latest = json.loads(
        (root / "records" / proposal["decision_id"] / "latest.json").read_text(encoding="utf-8")
    )
    assert latest["entry_zone"]["lower"] != "0.00"
    assert latest["sizing_recommendation"]["quantity"] is not None
