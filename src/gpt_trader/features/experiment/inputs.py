"""Strict, self-contained experiment inputs and reproducible execution assumptions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from gpt_trader.core import Candle
from gpt_trader.core.fill_accounting import aware_time, decimal_value, semantic_checksum

ENGINE_VERSION = "recorded-spot-v2"
# Deterministic rule channels a run may select. The choice is bound into the
# input identity, so a journal can never change channel on resume.
BENCHMARKS = ("baseline", "mean-reversion")
DEFAULT_BENCHMARK = "baseline"


@lru_cache(maxsize=1)
def implementation_digest() -> str:
    """Bind resumes to installed source, including transitive strategy defaults."""
    package = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Settings:
    initial_cash: Decimal = Decimal("1000")
    risk_fraction: Decimal = Decimal("0.01")
    exposure_fraction: Decimal = Decimal("0.20")
    drawdown_fraction: Decimal = Decimal("0.10")
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    quantity_increment: Decimal = Decimal("0.00000001")

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not value.is_finite() or value < 0:
                raise ValueError(f"Invalid experiment setting: {name}")
        for name in ("risk_fraction", "exposure_fraction", "drawdown_fraction"):
            if not Decimal(0) < getattr(self, name) <= Decimal(1):
                raise ValueError(f"{name} must be in (0, 1]")
        if self.initial_cash <= 0 or self.quantity_increment <= 0:
            raise ValueError("Initial cash and quantity increment must be positive")
        if self.fee_bps >= 10000 or self.slippage_bps >= 10000:
            raise ValueError("Costs must be below 10000 basis points")

    def to_dict(self) -> dict[str, str]:
        return {name: str(value) for name, value in asdict(self).items()}


@dataclass(frozen=True)
class ExperimentInput:
    payload: dict[str, Any]
    candles: tuple[Candle, ...]
    settings: Settings
    benchmark: str = DEFAULT_BENCHMARK
    # Bars before this index are indicator history only: no decision is taken.
    scored_index: int = 0

    @property
    def identity(self) -> str:
        return semantic_checksum(
            {
                "engine": ENGINE_VERSION,
                "implementation": implementation_digest(),
                "input": self.payload,
            }
        )

    @property
    def symbol(self) -> str:
        return str(self.payload["symbol"])


def parse_input(payload: dict[str, Any]) -> ExperimentInput:
    """Reject ambiguous provenance, unfinished/stale bars and malformed money."""
    required = {"schema_version", "source", "symbol", "recorded_at", "candles"}
    if set(payload) - required - {"settings", "benchmark", "scored_from"} or not required <= set(
        payload
    ):
        raise ValueError("Experiment requires schema_version, source, symbol, recorded_at, candles")
    if payload["schema_version"] != 1:
        raise ValueError("Unsupported experiment input version")
    if not isinstance(payload["source"], str) or not payload["source"].strip():
        raise ValueError("Recorded data requires a source label")
    if not isinstance(payload["symbol"], str) or not re.fullmatch(
        r"[A-Z0-9]{2,12}-USD", payload["symbol"]
    ):
        raise ValueError("Experiment supports one USD spot symbol")
    recorded_at = aware_time(payload["recorded_at"])
    raw_settings = payload.get("settings", {})
    if not isinstance(raw_settings, dict) or set(raw_settings) - set(Settings.__dataclass_fields__):
        raise ValueError("Unknown experiment settings")
    settings = Settings(**{key: decimal_value(value) for key, value in raw_settings.items()})
    rows = payload["candles"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError("Supply between 1 and 2000 hourly bars")
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "ts",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }:
            raise ValueError("Each bar requires ts, open, high, low, close, volume")
        candle = Candle(
            ts=aware_time(row["ts"]),
            **{
                name: decimal_value(row[name], positive=True)
                for name in ("open", "high", "low", "close")
            },
            volume=decimal_value(row["volume"]),
        )
        if (
            not candle.low
            <= min(candle.open, candle.close)
            <= max(candle.open, candle.close)
            <= candle.high
        ):
            raise ValueError("Inconsistent OHLC bar")
        if candle.volume < 0:
            raise ValueError("Volume must be nonnegative")
        if candle.ts + timedelta(hours=1) > recorded_at:
            raise ValueError("Unfinished bar after recorded_at")
        if candles and candle.ts != candles[-1].ts + timedelta(hours=1):
            raise ValueError("Stale, duplicate or unordered bars: contiguous hourly data required")
        candles.append(candle)
    if recorded_at - (candles[-1].ts + timedelta(hours=1)) >= timedelta(hours=1):
        raise ValueError("Last bar is stale at recorded_at")
    benchmark = payload.get("benchmark", DEFAULT_BENCHMARK)
    if benchmark not in BENCHMARKS:
        raise ValueError(f"Unknown benchmark; choose one of {', '.join(BENCHMARKS)}")
    scored_index = 0
    if "scored_from" in payload:
        scored_from = aware_time(payload["scored_from"])
        matches = [index for index, candle in enumerate(candles) if candle.ts == scored_from]
        if not matches:
            raise ValueError("scored_from must be the start time of a supplied bar")
        scored_index = matches[0]
    # Store a detached copy: later caller mutation must not change the experiment identity.
    normalized = json.loads(json.dumps(payload))
    normalized["settings"] = settings.to_dict()
    normalized["benchmark"] = benchmark
    normalized["scored_from"] = normalized["candles"][scored_index]["ts"]
    return ExperimentInput(normalized, tuple(candles), settings, benchmark, scored_index)


def with_benchmark(payload: dict[str, Any], benchmark: str | None) -> dict[str, Any]:
    """Apply an operator benchmark choice; refuse one that contradicts the file."""
    if benchmark is None:
        return payload
    if payload.get("benchmark", benchmark) != benchmark:
        raise ValueError("Input file names a different benchmark")
    return {**payload, "benchmark": benchmark}


def load_input(path: Path, *, benchmark: str | None = None) -> ExperimentInput:
    return parse_input(with_benchmark(json.loads(path.read_text(encoding="utf-8")), benchmark))
