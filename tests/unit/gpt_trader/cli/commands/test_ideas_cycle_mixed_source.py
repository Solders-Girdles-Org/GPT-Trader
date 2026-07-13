"""Mixed Coinbase/Alpaca read-only source tests for ``ideas cycle``."""

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
    TimeHorizon,
    TradeIdeaService,
)
from tests.unit.gpt_trader.cli.commands.conftest import attest_ideas_root
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    return exit_code, json.loads(capsys.readouterr().out)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _flat_series(symbol: str, *, as_of: datetime, granularity: str) -> SymbolSeries:
    step = timedelta(days=1) if granularity == "ONE_DAY" else timedelta(hours=1)
    start = as_of - (step * 60)
    return SymbolSeries(
        symbol=symbol,
        granularity=granularity,
        candles=tuple(
            Candle(
                ts=start + (step * index),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("10"),
            )
            for index in range(59)
        ),
    )


def _install_fake_builder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    fail: bool = False,
) -> list[tuple[tuple[str, ...], str, int, datetime]]:
    requests: list[tuple[tuple[str, ...], str, int, datetime]] = []

    async def fake_build(args: Any, request: Any) -> MarketSnapshot:
        requests.append((request.symbols, request.granularity, request.lookback, request.as_of))
        if fail:
            raise RuntimeError(f"{provider.title()} data unavailable")
        return MarketSnapshot(
            as_of=request.as_of,
            source=f"test:fake-{provider}",
            series=tuple(
                _flat_series(
                    symbol,
                    as_of=request.as_of,
                    granularity=request.granularity,
                )
                for symbol in request.symbols
            ),
        )

    helper = (
        "_build_coinbase_market_snapshot"
        if provider == "coinbase"
        else "_build_alpaca_equities_market_snapshot"
    )
    monkeypatch.setattr(f"gpt_trader.cli.commands.ideas.{helper}", fake_build)
    return requests


def _seed_busy_instrument(root: Path, instrument: str) -> None:
    TradeIdeaService(root).propose(
        build_trade_idea(
            decision_id=(
                f"trade-{datetime.now(UTC):%Y%m%d}-" f"{instrument.replace('-', '').lower()}-busy"
            ),
            instrument=instrument,
            time_horizon=TimeHorizon(
                expected_hold="3-10 days",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            ),
        ),
        actor_id="test-proposer",
    )


def _mixed_args(root: Path) -> list[str]:
    return [
        "ideas",
        "cycle",
        "--from-coinbase",
        "--from-alpaca",
        "--symbols",
        "BTC-USD",
        "--equity-symbols",
        "AAPL",
        "--granularity",
        "ONE_HOUR",
        "--lookback",
        "60",
        *_root_args(root),
    ]


def test_cycle_mixed_source_composes_one_attested_snapshot(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    coinbase_requests = _install_fake_builder(monkeypatch, provider="coinbase")
    alpaca_requests = _install_fake_builder(monkeypatch, provider="alpaca")
    argv = _mixed_args(root)
    argv[argv.index("BTC-USD")] = "BTC-USD,ETH-USD"
    argv[argv.index("AAPL")] = "AAPL,MSFT"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    snapshot = response["data"]["snapshot"]
    assert snapshot["symbols"] == ["BTC-USD", "ETH-USD", "AAPL", "MSFT"]
    assert snapshot["source"] == "composite[test:fake-coinbase;test:fake-alpaca]"
    assert [request[:3] for request in coinbase_requests] == [
        (("BTC-USD", "ETH-USD"), "ONE_HOUR", 60)
    ]
    assert [request[:3] for request in alpaca_requests] == [(("AAPL", "MSFT"), "ONE_DAY", 60)]
    assert coinbase_requests[0][3] == alpaca_requests[0][3]
    manifest = json.loads((root / "cycle" / "manifest.jsonl").read_text().splitlines()[0])
    assert manifest["snapshot"]["as_of"] == alpaca_requests[0][3].isoformat()


@pytest.mark.parametrize(
    ("argv", "expected_message"),
    [
        (
            ["ideas", "cycle", "--snapshot", "snapshot.json", "--from-alpaca"],
            "requires --from-coinbase",
        ),
        (
            [
                "ideas",
                "cycle",
                "--from-coinbase",
                "--from-alpaca",
                "--symbols",
                "BTC-USD",
                "--granularity",
                "ONE_HOUR",
                "--lookback",
                "60",
            ],
            "requires --equity-symbols",
        ),
        (
            [
                "ideas",
                "cycle",
                "--from-coinbase",
                "--symbols",
                "BTC-USD",
                "--equity-symbols",
                "AAPL",
                "--granularity",
                "ONE_HOUR",
                "--lookback",
                "60",
            ],
            "requires --from-alpaca",
        ),
        (
            [
                "ideas",
                "cycle",
                "--from-coinbase",
                "--symbols",
                "AAPL",
                "--granularity",
                "ONE_HOUR",
                "--lookback",
                "60",
            ],
            "accepts only crypto instruments",
        ),
        (
            [
                "ideas",
                "cycle",
                "--from-coinbase",
                "--from-alpaca",
                "--symbols",
                "BTC-USD",
                "--equity-symbols",
                "ETH-USD",
                "--granularity",
                "ONE_HOUR",
                "--lookback",
                "60",
            ],
            "accepts only equity instruments",
        ),
    ],
)
def test_cycle_mixed_source_rejects_invalid_routes_before_dispatch(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected_message: str,
) -> None:
    coinbase_requests = _install_fake_builder(monkeypatch, provider="coinbase")
    alpaca_requests = _install_fake_builder(monkeypatch, provider="alpaca")
    if "snapshot.json" in argv:
        (tmp_path / "snapshot.json").write_text("{}", encoding="utf-8")
        argv = [
            str(tmp_path / "snapshot.json") if item == "snapshot.json" else item for item in argv
        ]

    exit_code, response = _run_json(capsys, [*argv, *_root_args(tmp_path / "ideas")])

    assert exit_code != 0
    assert expected_message in response["errors"][0]["message"]
    assert coinbase_requests == []
    assert alpaca_requests == []


def test_cycle_mixed_source_primary_failure_fails_the_turn(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    _install_fake_builder(monkeypatch, provider="coinbase")
    _install_fake_builder(monkeypatch, provider="alpaca", fail=True)

    exit_code, response = _run_json(capsys, _mixed_args(root))

    assert exit_code != 0
    assert response["success"] is False
    manifest = json.loads((root / "cycle" / "manifest.jsonl").read_text().splitlines()[0])
    assert manifest["outcome"] == "failed"
    assert "Alpaca data unavailable" in manifest["error"]
    assert "snapshot" not in manifest


def test_cycle_mixed_source_exposes_no_alpaca_url_override(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                *_mixed_args(tmp_path / "ideas"),
                "--alpaca-data-base-url",
                "https://example.invalid",
            ]
        )

    assert error.value.code == 2
    assert "unrecognized arguments: --alpaca-data-base-url" in capsys.readouterr().err


def test_cycle_mixed_source_routes_busy_top_ups_by_asset_class(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    _seed_busy_instrument(root, "ETH-USD")
    _seed_busy_instrument(root, "AAPL")
    coinbase_requests = _install_fake_builder(monkeypatch, provider="coinbase")
    alpaca_requests = _install_fake_builder(monkeypatch, provider="alpaca")
    argv = _mixed_args(root)
    argv[argv.index("AAPL")] = "MSFT"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    assert response["data"]["snapshot"]["symbols"] == ["BTC-USD", "MSFT", "AAPL", "ETH-USD"]
    assert [request[:3] for request in coinbase_requests] == [
        (("BTC-USD",), "ONE_HOUR", 60),
        (("ETH-USD",), "ONE_HOUR", 60),
    ]
    assert [request[:3] for request in alpaca_requests] == [
        (("MSFT",), "ONE_DAY", 60),
        (("AAPL",), "ONE_DAY", 60),
    ]
