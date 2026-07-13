from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.commands import ideas
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas import MarketSnapshot, SymbolSeries

AS_OF = datetime(2035, 6, 12, 0, 0, tzinfo=UTC)
GOLDEN_CROSS = ["100"] * 50 + ["102", "104", "106"]


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _snapshot(symbol: str = "BTC-USD") -> MarketSnapshot:
    candles = []
    for index, close in enumerate(GOLDEN_CROSS):
        price = Decimal(close)
        candles.append(
            Candle(
                ts=AS_OF - timedelta(days=len(GOLDEN_CROSS) - index),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("5000") if index == len(GOLDEN_CROSS) - 1 else Decimal("1000"),
            )
        )
    return MarketSnapshot(
        as_of=AS_OF,
        source=(
            "coinbase:market-candles:granularity=ONE_DAY:lookback=53" f":as_of={AS_OF.isoformat()}"
        ),
        series=(SymbolSeries(symbol=symbol, granularity="ONE_DAY", candles=tuple(candles)),),
    )


def _minimal_snapshot(symbol: str, granularity: str) -> MarketSnapshot:
    price = Decimal("100")
    return MarketSnapshot(
        as_of=AS_OF,
        source=f"test:source:granularity={granularity}:lookback=1:as_of={AS_OF.isoformat()}",
        series=(
            SymbolSeries(
                symbol=symbol,
                granularity=granularity,
                candles=(
                    Candle(
                        ts=AS_OF - timedelta(days=1),
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=Decimal("1000"),
                    ),
                ),
            ),
        ),
    )


def _snapshot_build_args(out: Path) -> list[str]:
    return [
        "ideas",
        "snapshot",
        "build",
        "--from-coinbase",
        "--symbols",
        "BTC-USD",
        "--granularity",
        "ONE_DAY",
        "--lookback",
        "53",
        "--as-of",
        AS_OF.isoformat(),
        "--out",
        str(out),
        "--format",
        "json",
    ]


def test_snapshot_build_writes_json_accepted_by_propose_baseline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "snapshot.json"
    ideas_root = tmp_path / "ideas"
    captured: dict[str, Any] = {}

    async def fake_build(args: Any, request: Any) -> MarketSnapshot:
        captured["request"] = request
        return _snapshot()

    monkeypatch.setattr(ideas, "_build_coinbase_market_snapshot", fake_build)

    exit_code, response = _run_json(capsys, _snapshot_build_args(out))

    assert exit_code == 0
    assert response["command"] == "ideas snapshot build"
    assert response["data"]["out"] == str(out)
    assert response["data"]["snapshot"]["symbols"] == ["BTC-USD"]
    assert captured["request"].symbols == ("BTC-USD",)
    assert out.exists()

    propose_exit_code, propose_response = _run_json(
        capsys,
        [
            "ideas",
            "propose-baseline",
            "--ideas-root",
            str(ideas_root),
            "--format",
            "json",
            "--snapshot",
            str(out),
        ],
    )

    assert propose_exit_code == 0
    assert propose_response["data"]["proposal_count"] == 1
    assert propose_response["data"]["proposed"][0]["decision_id"].startswith(
        "trade-20350612-btcusd-"
    )


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [("1H", "ONE_HOUR"), ("4H", "FOUR_HOUR"), ("1D", "ONE_DAY")],
)
def test_snapshot_build_normalizes_granularity_alias_before_fetch(
    alias: str,
    canonical: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_build(args: Any, request: Any) -> MarketSnapshot:
        captured["request"] = request
        return _minimal_snapshot(request.symbols[0], request.granularity)

    monkeypatch.setattr(ideas, "_build_coinbase_market_snapshot", fake_build)
    argv = _snapshot_build_args(tmp_path / "snapshot.json")
    argv[argv.index("--granularity") + 1] = alias

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    assert captured["request"].granularity == canonical
    assert response["data"]["snapshot"]["series"][0]["granularity"] == canonical


def test_snapshot_build_rejects_as_of_without_timezone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build(args: Any, request: Any) -> MarketSnapshot:
        raise AssertionError("snapshot build should reject input before fetching")

    monkeypatch.setattr(ideas, "_build_coinbase_market_snapshot", fake_build)
    argv = _snapshot_build_args(tmp_path / "snapshot.json")
    argv[argv.index("--as-of") + 1] = "2035-06-12T00:00:00"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "as_of"


def test_snapshot_build_rejects_unsupported_granularity_before_fetch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build(args: Any, request: Any) -> MarketSnapshot:
        raise AssertionError("snapshot build should reject input before fetching")

    monkeypatch.setattr(ideas, "_build_coinbase_market_snapshot", fake_build)
    argv = _snapshot_build_args(tmp_path / "snapshot.json")
    argv[argv.index("--granularity") + 1] = "BAD"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "granularity"


def test_snapshot_build_does_not_accept_response_output_sink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build(args: Any, request: Any) -> MarketSnapshot:
        raise AssertionError("snapshot build should reject input before fetching")

    monkeypatch.setattr(ideas, "_build_coinbase_market_snapshot", fake_build)
    out = tmp_path / "snapshot.json"
    out.write_text('{"snapshot": true}\n', encoding="utf-8")
    argv = [*_snapshot_build_args(out), "--output", str(out)]

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "output"
    assert json.loads(out.read_text(encoding="utf-8")) == {"snapshot": True}


def test_snapshot_build_from_stooq_routes_to_equities_builder(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "snapshot.json"
    captured: dict[str, Any] = {}

    async def fake_equities_build(request: Any, **kwargs: Any) -> MarketSnapshot:
        captured["request"] = request
        captured["kwargs"] = kwargs
        return _minimal_snapshot("AAPL", "ONE_DAY")

    async def fail_coinbase_build(args: Any, request: Any) -> MarketSnapshot:
        raise AssertionError("--from-stooq must not route to the Coinbase builder")

    monkeypatch.setattr(ideas, "build_stooq_equities_market_snapshot", fake_equities_build)
    monkeypatch.setattr(ideas, "_build_coinbase_market_snapshot", fail_coinbase_build)
    argv = _snapshot_build_args(out)
    argv[argv.index("--from-coinbase")] = "--from-stooq"
    argv[argv.index("--symbols") + 1] = "AAPL"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    assert captured["request"].symbols == ("AAPL",)
    assert captured["kwargs"]["source_label"] == "stooq:market-candles"
    assert captured["kwargs"]["base_url"] == "https://stooq.com"
    assert response["data"]["snapshot"]["symbols"] == ["AAPL"]
    assert out.exists()


def test_snapshot_build_from_alpaca_routes_to_alpaca_builder(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "snapshot.json"
    captured: dict[str, Any] = {}

    async def fake_alpaca_build(request: Any, **kwargs: Any) -> MarketSnapshot:
        captured["request"] = request
        captured["kwargs"] = kwargs
        return _minimal_snapshot("AAPL", "ONE_DAY")

    async def fail_coinbase_build(args: Any, request: Any) -> MarketSnapshot:
        raise AssertionError("--from-alpaca must not route to the Coinbase builder")

    async def fail_stooq_build(request: Any, **kwargs: Any) -> MarketSnapshot:
        raise AssertionError("--from-alpaca must not route to the Stooq builder")

    monkeypatch.setattr(ideas, "build_alpaca_equities_market_snapshot", fake_alpaca_build)
    monkeypatch.setattr(ideas, "build_stooq_equities_market_snapshot", fail_stooq_build)
    monkeypatch.setattr(ideas, "_build_coinbase_market_snapshot", fail_coinbase_build)
    argv = _snapshot_build_args(out)
    argv[argv.index("--from-coinbase")] = "--from-alpaca"
    argv[argv.index("--symbols") + 1] = "AAPL"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    assert captured["request"].symbols == ("AAPL",)
    assert captured["kwargs"]["source_label"] == "alpaca:market-candles"
    assert captured["kwargs"]["base_url"] == "https://data.alpaca.markets"
    assert response["data"]["snapshot"]["symbols"] == ["AAPL"]
    assert out.exists()


def test_snapshot_build_rejects_duplicate_symbols(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = _snapshot_build_args(tmp_path / "snapshot.json")
    argv[argv.index("--symbols") + 1] = "BTC-USD,btc-usd"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "symbols"
