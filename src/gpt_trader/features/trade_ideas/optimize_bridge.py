"""Bridge optimize parameter exports into proposer replay calibration."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from gpt_trader.core import Candle
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.baseline import BaselineProposer, BaselineProposerConfig
from gpt_trader.features.trade_ideas.replay import (
    ReplayReport,
    ReplayRunnerConfig,
    TradeIdeaReplayRunner,
)

REPLAY_OPTIMIZE_OBJECTIVES = ("target-hit-rate", "average-return-r")


@dataclass(frozen=True, slots=True)
class OptimizeBaselineCandidate:
    """One optimize-sourced baseline proposer config candidate."""

    candidate_id: str
    parameters: dict[str, Any]
    config: BaselineProposerConfig
    optimize_objective_value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OptimizeBaselineReplayRow:
    """Replay result for one optimize-sourced candidate."""

    rank: int
    candidate_id: str
    proposer_id: str
    parameters: dict[str, Any]
    replay_objective_value: Decimal
    optimize_objective_value: Decimal | None
    report: ReplayReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "candidate_id": self.candidate_id,
            "proposer_id": self.proposer_id,
            "parameters": self.parameters,
            "replay_objective_value": str(self.replay_objective_value),
            "optimize_objective_value": (
                str(self.optimize_objective_value)
                if self.optimize_objective_value is not None
                else None
            ),
            "report": self.report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class OptimizeBaselineReplayReport:
    """Ranked replay calibration report for optimize-sourced configs."""

    study_path: Path
    objective: str
    symbol: str
    granularity: str
    source: str
    snapshots_evaluated: int
    rows: tuple[OptimizeBaselineReplayRow, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_path": str(self.study_path),
            "objective": self.objective,
            "symbol": self.symbol,
            "granularity": self.granularity,
            "source": self.source,
            "snapshots_evaluated": self.snapshots_evaluated,
            "candidate_count": len(self.rows),
            "rankings": [row.to_dict() for row in self.rows],
        }


def load_optimize_baseline_candidates(
    path: Path,
    *,
    base_config: BaselineProposerConfig,
) -> tuple[OptimizeBaselineCandidate, ...]:
    """Load baseline proposer candidates from a JSON optimize export."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValidationError(f"Could not read optimize study: {error}", field="study") from error
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"Optimize study must be valid JSON: {error.msg}",
            field="study",
        ) from error

    raw_candidates = _raw_candidates(payload)
    candidates = tuple(
        _candidate_from_payload(index, item, base_config=base_config)
        for index, item in enumerate(raw_candidates, start=1)
    )
    if not candidates:
        raise ValidationError(
            "Optimize study did not contain any baseline proposer candidates",
            field="study",
        )
    return candidates


def replay_optimize_baseline_candidates(
    candidates: tuple[OptimizeBaselineCandidate, ...],
    *,
    study_path: Path,
    objective: str,
    symbol: str,
    granularity: str,
    candles: Sequence[Candle],
    replay_config: ReplayRunnerConfig,
) -> OptimizeBaselineReplayReport:
    """Replay and rank optimize-sourced baseline proposer candidates."""
    if objective not in REPLAY_OPTIMIZE_OBJECTIVES:
        raise ValidationError(
            f"Unsupported replay optimize objective: {objective}",
            field="objective",
        )
    rows: list[OptimizeBaselineReplayRow] = []
    for candidate in candidates:
        report = TradeIdeaReplayRunner(
            BaselineProposer(candidate.config),
            config=replay_config,
        ).run_series(symbol=symbol, granularity=granularity, candles=candles)
        rows.append(
            OptimizeBaselineReplayRow(
                rank=0,
                candidate_id=candidate.candidate_id,
                proposer_id=report.proposer_id,
                parameters=candidate.parameters,
                replay_objective_value=_replay_objective_value(report, objective),
                optimize_objective_value=candidate.optimize_objective_value,
                report=report,
            )
        )
    ranked_rows = sorted(
        rows,
        key=lambda item: (
            -item.replay_objective_value,
            -_average_return_value(item.report),
            -Decimal(item.report.ideas_proposed),
            item.candidate_id,
        ),
    )
    ranked = tuple(
        OptimizeBaselineReplayRow(
            rank=index,
            candidate_id=row.candidate_id,
            proposer_id=row.proposer_id,
            parameters=row.parameters,
            replay_objective_value=row.replay_objective_value,
            optimize_objective_value=row.optimize_objective_value,
            report=row.report,
        )
        for index, row in enumerate(ranked_rows, start=1)
    )
    snapshots = ranked[0].report.snapshots_evaluated if ranked else 0
    return OptimizeBaselineReplayReport(
        study_path=study_path,
        objective=objective,
        symbol=symbol,
        granularity=granularity,
        source=replay_config.source,
        snapshots_evaluated=snapshots,
        rows=ranked,
    )


def optimize_replay_min_history(candidates: tuple[OptimizeBaselineCandidate, ...]) -> int:
    """Return the minimum shared replay history needed for all candidates."""
    return max(
        max(candidate.config.short_window, candidate.config.long_window)
        + candidate.config.crossover_lookback
        for candidate in candidates
    )


def _raw_candidates(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValidationError("Optimize study must be a JSON object", field="study")
    if isinstance(payload.get("candidates"), list):
        return [item for item in payload["candidates"] if isinstance(item, dict)]
    if isinstance(payload.get("window_results"), list):
        return [
            item
            for item in payload["window_results"]
            if isinstance(item, dict) and _is_replayable_window_result(item)
        ]
    if isinstance(payload.get("trials"), list):
        trials = [
            item
            for item in payload["trials"]
            if isinstance(item, dict) and item.get("is_feasible", True)
        ]
        # A normal `optimize export --include-trials` payload still carries
        # top-level best_parameters; an empty/infeasible trials list must fall
        # through to it rather than yielding zero candidates.
        if trials:
            return trials
    if isinstance(payload.get("best_parameters"), dict):
        return [payload]
    if isinstance(payload.get("parameters"), dict):
        return [payload]
    return []


def _is_replayable_window_result(item: dict[str, Any]) -> bool:
    if item.get("is_valid") is False:
        return False
    parameters = _first_present(item, ("parameters", "best_parameters"))
    return isinstance(parameters, dict) and bool(parameters)


def _candidate_from_payload(
    index: int,
    item: dict[str, Any],
    *,
    base_config: BaselineProposerConfig,
) -> OptimizeBaselineCandidate:
    parameters = _parameters_from_candidate(item)
    raw_candidate_id = _first_present(item, ("candidate_id", "id", "trial_number", "window_id"))
    candidate_id = str(raw_candidate_id) if raw_candidate_id is not None else f"candidate-{index}"
    objective_value = item.get("objective_value", item.get("best_objective_value"))
    return OptimizeBaselineCandidate(
        candidate_id=candidate_id,
        parameters=parameters,
        config=_baseline_config_from_parameters(parameters, base_config),
        optimize_objective_value=(
            Decimal(str(objective_value)) if objective_value is not None else None
        ),
    )


def _parameters_from_candidate(item: dict[str, Any]) -> dict[str, Any]:
    parameters = _first_present(item, ("parameters", "best_parameters"))
    if parameters is None:
        parameters = item
    if not isinstance(parameters, dict):
        raise ValidationError("Optimize candidate parameters must be an object", field="study")
    return dict(parameters)


def _first_present(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = item.get(name)
        if value is not None:
            return value
    return None


def _baseline_config_from_parameters(
    parameters: dict[str, Any],
    base_config: BaselineProposerConfig,
) -> BaselineProposerConfig:
    return BaselineProposerConfig(
        short_window=_int_param(
            parameters, ("short_window", "short_ma_period"), base_config.short_window
        ),
        long_window=_int_param(
            parameters, ("long_window", "long_ma_period"), base_config.long_window
        ),
        crossover_lookback=_int_param(
            parameters,
            ("crossover_lookback",),
            base_config.crossover_lookback,
        ),
        risk_per_idea_pct=_decimal_param(
            parameters,
            ("risk_per_idea_pct",),
            base_config.risk_per_idea_pct,
        ),
        entry_band_pct=_decimal_param(parameters, ("entry_band_pct",), base_config.entry_band_pct),
        reward_multiple=_decimal_param(
            parameters, ("reward_multiple",), base_config.reward_multiple
        ),
        expiry_hours=_int_param(parameters, ("expiry_hours",), base_config.expiry_hours),
        expected_hold=str(parameters.get("expected_hold", base_config.expected_hold)),
        price_precision=_decimal_param(
            parameters, ("price_precision",), base_config.price_precision
        ),
    )


def _int_param(parameters: dict[str, Any], names: tuple[str, ...], default: int) -> int:
    value = _first_param(parameters, names)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(
            f"Optimize parameter '{names[0]}' must be an integer",
            field="study",
        ) from error


def _decimal_param(
    parameters: dict[str, Any],
    names: tuple[str, ...],
    default: Decimal,
) -> Decimal:
    value = _first_param(parameters, names)
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValidationError(
            f"Optimize parameter '{names[0]}' must be numeric",
            field="study",
        ) from error


def _first_param(parameters: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in parameters:
            return parameters[name]
    return None


def _replay_objective_value(report: ReplayReport, objective: str) -> Decimal:
    if objective == "average-return-r":
        return _average_return_value(report)
    return report.target_hit_rate


def _average_return_value(report: ReplayReport) -> Decimal:
    return report.average_return_r if report.average_return_r is not None else Decimal("-999")


__all__ = [
    "OptimizeBaselineCandidate",
    "OptimizeBaselineReplayReport",
    "OptimizeBaselineReplayRow",
    "REPLAY_OPTIMIZE_OBJECTIVES",
    "load_optimize_baseline_candidates",
    "optimize_replay_min_history",
    "replay_optimize_baseline_candidates",
]
