"""Replay scoring for proposer-generated trade ideas.

This module keeps calibration separate from execution. It feeds historical,
point-in-time snapshots to a proposer, then scores the emitted records against
subsequent candles using only the plan already written on the trade idea.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from gpt_trader.core import Candle
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.eligibility import evaluate_eligibility
from gpt_trader.features.trade_ideas.models import TradeDirection, TradeIdea
from gpt_trader.features.trade_ideas.proposer import Proposer
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot, SymbolSeries


class ReplayScoringError(ValidationError):
    """Raised when a trade idea cannot be scored from its record."""


class ReplayOutcome(str, Enum):
    """Terminal outcome for a replay-scored idea."""

    TARGET_HIT = "target_hit"
    STOP_HIT = "stop_hit"
    TIMED_OUT = "timed_out"
    NOT_FILLED = "not_filled"
    NO_FUTURE_DATA = "no_future_data"


@dataclass(frozen=True, slots=True)
class ScoringLevels:
    """Numeric plan levels used to score a trade idea against future candles."""

    entry_lower: Decimal
    entry_upper: Decimal
    stop: Decimal
    target: Decimal

    @property
    def entry_midpoint(self) -> Decimal:
        return (self.entry_lower + self.entry_upper) / Decimal("2")


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Replay outcome for one proposed trade idea."""

    decision_id: str
    record_hash: str
    instrument: str
    direction: TradeDirection
    as_of: datetime
    expires_at: datetime
    outcome: ReplayOutcome
    levels: ScoringLevels
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    return_r: Decimal | None = None
    return_pct: Decimal | None = None
    bars_evaluated: int = 0
    # Notional the idea's sizing recommendation would have deployed; carries
    # the sizing channel into replay so capital-weighted aggregates can rank
    # proposers that differ only in how much they commit (#1244).
    sized_notional: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "record_hash": self.record_hash,
            "instrument": self.instrument,
            "direction": self.direction.value,
            "as_of": self.as_of.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "outcome": self.outcome.value,
            "levels": {
                "entry_lower": str(self.levels.entry_lower),
                "entry_upper": str(self.levels.entry_upper),
                "stop": str(self.levels.stop),
                "target": str(self.levels.target),
            },
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_price": str(self.entry_price) if self.entry_price is not None else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": str(self.exit_price) if self.exit_price is not None else None,
            "return_r": str(self.return_r) if self.return_r is not None else None,
            "return_pct": str(self.return_pct) if self.return_pct is not None else None,
            "bars_evaluated": self.bars_evaluated,
            "sized_notional": (
                str(self.sized_notional) if self.sized_notional is not None else None
            ),
        }


@runtime_checkable
class DiagnosticsProposer(Protocol):
    """Optional proposer surface exposing counterfactual decision counts.

    A proposer that filters or rewrites another proposer's output (the
    regime overlay) can report how often each channel fired; the replay
    runner attaches those counts to the report so a dead channel is visible
    in evidence output instead of requiring a manual audit (#1243). Counts
    are cumulative since proposer construction.
    """

    def replay_diagnostics(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Aggregate calibration report for a proposer replay run."""

    proposer_id: str
    symbol: str
    granularity: str
    source: str
    snapshots_evaluated: int
    ideas: tuple[ReplayResult, ...]
    eligibility_checked: int = 0
    eligibility_passed: int = 0
    proposer_diagnostics: Mapping[str, Any] | None = None

    @property
    def ideas_proposed(self) -> int:
        return len(self.ideas)

    @property
    def target_hits(self) -> int:
        return self._count(ReplayOutcome.TARGET_HIT)

    @property
    def stop_hits(self) -> int:
        return self._count(ReplayOutcome.STOP_HIT)

    @property
    def timed_out(self) -> int:
        return self._count(ReplayOutcome.TIMED_OUT)

    @property
    def not_filled(self) -> int:
        return self._count(ReplayOutcome.NOT_FILLED)

    @property
    def no_future_data(self) -> int:
        return self._count(ReplayOutcome.NO_FUTURE_DATA)

    @property
    def resolved_ideas(self) -> int:
        return self.target_hits + self.stop_hits + self.timed_out

    @property
    def target_hit_rate(self) -> Decimal:
        if self.resolved_ideas == 0:
            return Decimal("0")
        return Decimal(self.target_hits) / Decimal(self.resolved_ideas)

    @property
    def stop_hit_rate(self) -> Decimal:
        if self.resolved_ideas == 0:
            return Decimal("0")
        return Decimal(self.stop_hits) / Decimal(self.resolved_ideas)

    @property
    def average_return_r(self) -> Decimal | None:
        returns = [idea.return_r for idea in self.ideas if idea.return_r is not None]
        if not returns:
            return None
        return sum(returns, Decimal("0")) / Decimal(len(returns))

    @property
    def capital_weighted_average_return_r(self) -> Decimal | None:
        """Average R weighted by each idea's sized notional.

        Reported alongside — never replacing — the per-idea average: it is
        the only replay aggregate that can see the sizing channel, where two
        proposers with identical levels differ in how much they commit.
        """
        weighted = self._capital_weighted_returns()
        if not weighted:
            return None
        total_notional = sum((notional for _, notional in weighted), Decimal("0"))
        weighted_sum = sum((value * notional for value, notional in weighted), Decimal("0"))
        return weighted_sum / total_notional

    @property
    def capital_weighted_sample(self) -> int:
        """Resolved ideas that carry a positive sized notional."""
        return len(self._capital_weighted_returns())

    def _capital_weighted_returns(self) -> list[tuple[Decimal, Decimal]]:
        return [
            (idea.return_r, idea.sized_notional)
            for idea in self.ideas
            if idea.return_r is not None
            and idea.sized_notional is not None
            and idea.sized_notional > 0
        ]

    @property
    def eligibility_pass_rate(self) -> Decimal:
        if self.eligibility_checked == 0:
            return Decimal("0")
        return Decimal(self.eligibility_passed) / Decimal(self.eligibility_checked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposer_id": self.proposer_id,
            "symbol": self.symbol,
            "granularity": self.granularity,
            "source": self.source,
            "snapshots_evaluated": self.snapshots_evaluated,
            "ideas_proposed": self.ideas_proposed,
            "target_hits": self.target_hits,
            "stop_hits": self.stop_hits,
            "timed_out": self.timed_out,
            "not_filled": self.not_filled,
            "no_future_data": self.no_future_data,
            "resolved_ideas": self.resolved_ideas,
            "target_hit_rate": str(self.target_hit_rate),
            "stop_hit_rate": str(self.stop_hit_rate),
            "average_return_r": (
                str(self.average_return_r) if self.average_return_r is not None else None
            ),
            "capital_weighted_average_return_r": (
                str(self.capital_weighted_average_return_r)
                if self.capital_weighted_average_return_r is not None
                else None
            ),
            "capital_weighted_sample": self.capital_weighted_sample,
            "eligibility_checked": self.eligibility_checked,
            "eligibility_passed": self.eligibility_passed,
            "eligibility_pass_rate": str(self.eligibility_pass_rate),
            "proposer_diagnostics": (
                dict(self.proposer_diagnostics) if self.proposer_diagnostics is not None else None
            ),
            "ideas": [idea.to_dict() for idea in self.ideas],
        }

    def _count(self, outcome: ReplayOutcome) -> int:
        return sum(1 for idea in self.ideas if idea.outcome is outcome)


@dataclass(frozen=True, slots=True)
class ReplayTournamentRanking:
    """One ranked proposer row in a replay tournament.

    Ranking order stays on the per-idea metrics; the capital-weighted average
    is reported beside them so the sizing channel is visible without changing
    what the tournament optimizes.
    """

    rank: int
    proposer_id: str
    ideas_proposed: int
    resolved_ideas: int
    target_hit_rate: Decimal
    stop_hit_rate: Decimal
    average_return_r: Decimal | None
    eligibility_pass_rate: Decimal
    capital_weighted_average_return_r: Decimal | None = None
    capital_weighted_sample: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "proposer_id": self.proposer_id,
            "ideas_proposed": self.ideas_proposed,
            "resolved_ideas": self.resolved_ideas,
            "target_hit_rate": str(self.target_hit_rate),
            "stop_hit_rate": str(self.stop_hit_rate),
            "average_return_r": (
                str(self.average_return_r) if self.average_return_r is not None else None
            ),
            "eligibility_pass_rate": str(self.eligibility_pass_rate),
            "capital_weighted_average_return_r": (
                str(self.capital_weighted_average_return_r)
                if self.capital_weighted_average_return_r is not None
                else None
            ),
            "capital_weighted_sample": self.capital_weighted_sample,
        }


@dataclass(frozen=True, slots=True)
class ReplayTournamentReport:
    """Head-to-head replay comparison over one shared candle window."""

    symbol: str
    granularity: str
    source: str
    snapshots_evaluated: int
    reports: tuple[ReplayReport, ...]
    rankings: tuple[ReplayTournamentRanking, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "granularity": self.granularity,
            "source": self.source,
            "snapshots_evaluated": self.snapshots_evaluated,
            "proposer_count": len(self.reports),
            "rankings": [ranking.to_dict() for ranking in self.rankings],
            "reports": [report.to_dict() for report in self.reports],
        }


LevelExtractor = Callable[[TradeIdea], ScoringLevels]

_NUMBER_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_NON_PRICE_QUANTITY_SUFFIX_RE = re.compile(
    rf"\s*(?:[-/]\s*{_NUMBER_RE.pattern}\s*)?(?:"
    r"%|"
    r"[Rr]\b|"
    r"-(?:bar|bars|candle|candles|day|days|hour|hours|minute|minutes|week|weeks|month|months|period|periods)\b|"
    r"(?:bar|bars|candle|candles|trading\s+day|trading\s+days|day|days|hour|hours|minute|minutes|week|weeks|month|months|period|periods)\b"
    r")",
    re.IGNORECASE,
)
_DIRECT_STOP_RE = re.compile(
    rf"\b(?:close|price|stop|invalid(?:ation|ated)?|break|move|trade)\w*\s+"
    rf"(?:below|under|beneath|above|over)\s+\$?\s*({_NUMBER_RE.pattern})",
    re.IGNORECASE,
)


def extract_numeric_scoring_levels(idea: TradeIdea) -> ScoringLevels:
    """Extract numeric scoring levels from a broker-neutral trade idea.

    The trade-idea model currently stores invalidation and target as explicit
    prose fields. This extractor is intentionally narrow: it uses the structured
    entry zone and reads the numeric stop/target from those explicit text fields.
    Future proposers can pass a stricter ``LevelExtractor`` to avoid text
    extraction once numeric exit levels are part of the proposer contract.
    """
    if idea.entry_zone.lower is None or idea.entry_zone.upper is None:
        raise ReplayScoringError(
            f"Trade idea '{idea.decision_id}' needs numeric entry_zone lower and upper",
            field="entry_zone",
        )

    entry_midpoint = (idea.entry_zone.lower + idea.entry_zone.upper) / Decimal("2")
    stop = _extract_stop_level(
        idea.invalidation,
        idea.decision_id,
        direction=idea.direction,
        entry_midpoint=entry_midpoint,
    )
    target = _extract_target_level(
        idea.target_exit,
        idea.decision_id,
        direction=idea.direction,
        entry_midpoint=entry_midpoint,
    )
    levels = ScoringLevels(
        entry_lower=idea.entry_zone.lower,
        entry_upper=idea.entry_zone.upper,
        stop=stop,
        target=target,
    )
    _validate_level_order(idea, levels)
    return levels


def exit_plan_scoring_levels(idea: TradeIdea) -> ScoringLevels:
    """Scoring levels from the structured exit plan, falling back to text.

    Prefers the machine-readable ``exit_plan`` (#1218) over parsing the free-text
    invalidation/target fields, and delegates to
    ``extract_numeric_scoring_levels`` when no complete plan is present so
    human-authored and pre-exit-plan ideas still resolve.
    """
    plan = idea.exit_plan
    if (
        plan is not None
        and plan.stop is not None
        and plan.target is not None
        and idea.entry_zone.lower is not None
        and idea.entry_zone.upper is not None
    ):
        levels = ScoringLevels(
            entry_lower=idea.entry_zone.lower,
            entry_upper=idea.entry_zone.upper,
            stop=plan.stop,
            target=plan.target,
        )
        _validate_level_order(idea, levels)
        return levels
    return extract_numeric_scoring_levels(idea)


def score_trade_idea(
    idea: TradeIdea,
    *,
    as_of: datetime,
    future_candles: Sequence[Candle],
    level_extractor: LevelExtractor = extract_numeric_scoring_levels,
    candle_duration: timedelta | None = None,
) -> ReplayResult:
    """Score one trade idea against candles available after ``as_of``."""
    if idea.time_horizon.expires_at is None:
        raise ReplayScoringError(
            f"Trade idea '{idea.decision_id}' has no expiry to score against",
            field="time_horizon",
        )
    if idea.direction not in {TradeDirection.LONG, TradeDirection.SHORT}:
        raise ReplayScoringError(
            f"Trade idea '{idea.decision_id}' direction '{idea.direction.value}' is not scoreable",
            field="direction",
        )

    levels = level_extractor(idea)
    candles = tuple(
        sorted(
            (
                candle
                for candle in future_candles
                if as_of <= candle.ts
                and _candle_ends_by_expiry(
                    candle,
                    expires_at=idea.time_horizon.expires_at,
                    candle_duration=candle_duration,
                )
            ),
            key=lambda candle: candle.ts,
        )
    )
    if not candles:
        return _result(
            idea,
            as_of=as_of,
            levels=levels,
            outcome=ReplayOutcome.NO_FUTURE_DATA,
        )

    entry_candle = _first_entry_candle(candles, levels)
    if entry_candle is None:
        return _result(
            idea,
            as_of=as_of,
            levels=levels,
            outcome=ReplayOutcome.NOT_FILLED,
            bars_evaluated=len(candles),
        )

    entry_price = levels.entry_midpoint
    entry_index = candles.index(entry_candle)
    post_entry_candles = candles[entry_index:]
    for bars_after_entry, candle in enumerate(post_entry_candles, start=1):
        outcome_price = _bar_outcome_price(
            idea.direction,
            candle,
            levels,
            allow_favorable_exit=candle is not entry_candle,
            allow_adverse_exit=(
                candle is not entry_candle
                or not _entry_candle_proves_prefill_stop(idea.direction, candle, levels)
            ),
        )
        if outcome_price is None:
            continue
        outcome, exit_price = outcome_price
        return _result(
            idea,
            as_of=as_of,
            levels=levels,
            outcome=outcome,
            entry_time=entry_candle.ts,
            entry_price=entry_price,
            exit_time=candle.ts,
            exit_price=exit_price,
            bars_evaluated=entry_index + bars_after_entry,
        )

    timeout_candle = post_entry_candles[-1]
    return _result(
        idea,
        as_of=as_of,
        levels=levels,
        outcome=ReplayOutcome.TIMED_OUT,
        entry_time=entry_candle.ts,
        entry_price=entry_price,
        exit_time=timeout_candle.ts,
        exit_price=timeout_candle.close,
        bars_evaluated=len(candles),
    )


@dataclass(frozen=True, slots=True)
class ReplayRunnerConfig:
    """Configuration for replaying one historical candle series."""

    source: str = "historical:candles"
    min_history: int = 1

    def __post_init__(self) -> None:
        if self.min_history < 1:
            raise ReplayScoringError(
                "Replay runner min_history must be at least 1",
                field="min_history",
            )


class TradeIdeaReplayRunner:
    """Build point-in-time snapshots from history and score proposer output."""

    def __init__(
        self,
        proposer: Proposer,
        *,
        config: ReplayRunnerConfig | None = None,
        level_extractor: LevelExtractor = extract_numeric_scoring_levels,
    ) -> None:
        self._proposer = proposer
        self._config = config or ReplayRunnerConfig()
        self._level_extractor = level_extractor

    def run_series(
        self,
        *,
        symbol: str,
        granularity: str,
        candles: Sequence[Candle],
    ) -> ReplayReport:
        ordered_candles = tuple(sorted(candles, key=lambda candle: candle.ts))
        results: list[ReplayResult] = []
        snapshots_evaluated = 0
        eligibility_checked = 0
        eligibility_passed = 0

        for index in range(self._config.min_history, len(ordered_candles)):
            as_of = ordered_candles[index].ts
            history = ordered_candles[:index]
            future = ordered_candles[index:]
            snapshot = MarketSnapshot(
                as_of=as_of,
                source=self._config.source,
                series=(
                    SymbolSeries(
                        symbol=symbol,
                        granularity=granularity,
                        candles=history,
                    ),
                ),
            )
            snapshots_evaluated += 1
            for idea in self._proposer.propose(snapshot):
                eligibility_checked += 1
                if not evaluate_eligibility(idea):
                    eligibility_passed += 1
                results.append(
                    score_trade_idea(
                        idea,
                        as_of=as_of,
                        future_candles=future,
                        level_extractor=self._level_extractor,
                        candle_duration=_granularity_duration(granularity),
                    )
                )

        return ReplayReport(
            proposer_id=self._proposer.proposer_id,
            symbol=symbol,
            granularity=granularity,
            source=self._config.source,
            snapshots_evaluated=snapshots_evaluated,
            ideas=tuple(results),
            eligibility_checked=eligibility_checked,
            eligibility_passed=eligibility_passed,
            proposer_diagnostics=(
                dict(self._proposer.replay_diagnostics())
                if isinstance(self._proposer, DiagnosticsProposer)
                else None
            ),
        )


class TradeIdeaReplayTournamentRunner:
    """Replay multiple proposers over identical point-in-time snapshots."""

    def __init__(
        self,
        proposers: Sequence[Proposer],
        *,
        config: ReplayRunnerConfig | None = None,
        level_extractor: LevelExtractor = extract_numeric_scoring_levels,
    ) -> None:
        if not proposers:
            raise ReplayScoringError(
                "Replay tournament needs at least one proposer",
                field="proposers",
            )
        proposer_ids = [proposer.proposer_id for proposer in proposers]
        duplicates = sorted(
            proposer_id for proposer_id in set(proposer_ids) if proposer_ids.count(proposer_id) > 1
        )
        if duplicates:
            raise ReplayScoringError(
                f"Replay tournament proposer ids must be unique: {', '.join(duplicates)}",
                field="proposers",
            )
        self._proposers = tuple(proposers)
        self._config = config or ReplayRunnerConfig()
        self._level_extractor = level_extractor

    def run_series(
        self,
        *,
        symbol: str,
        granularity: str,
        candles: Sequence[Candle],
    ) -> ReplayTournamentReport:
        reports = tuple(
            TradeIdeaReplayRunner(
                proposer,
                config=self._config,
                level_extractor=self._level_extractor,
            ).run_series(symbol=symbol, granularity=granularity, candles=candles)
            for proposer in self._proposers
        )
        snapshots = reports[0].snapshots_evaluated
        return ReplayTournamentReport(
            symbol=symbol,
            granularity=granularity,
            source=self._config.source,
            snapshots_evaluated=snapshots,
            reports=reports,
            rankings=_rank_tournament_reports(reports),
        )


def _rank_tournament_reports(
    reports: Sequence[ReplayReport],
) -> tuple[ReplayTournamentRanking, ...]:
    ordered = sorted(
        reports,
        key=lambda report: (
            report.average_return_r if report.average_return_r is not None else Decimal("-999"),
            report.target_hit_rate,
            -report.stop_hit_rate,
            report.ideas_proposed,
            report.proposer_id,
        ),
        reverse=True,
    )
    return tuple(
        ReplayTournamentRanking(
            rank=index,
            proposer_id=report.proposer_id,
            ideas_proposed=report.ideas_proposed,
            resolved_ideas=report.resolved_ideas,
            target_hit_rate=report.target_hit_rate,
            stop_hit_rate=report.stop_hit_rate,
            average_return_r=report.average_return_r,
            eligibility_pass_rate=report.eligibility_pass_rate,
            capital_weighted_average_return_r=report.capital_weighted_average_return_r,
            capital_weighted_sample=report.capital_weighted_sample,
        )
        for index, report in enumerate(ordered, start=1)
    )


def _extract_stop_level(
    text: str,
    decision_id: str,
    *,
    direction: TradeDirection,
    entry_midpoint: Decimal,
) -> Decimal:
    candidates = _price_level_numbers(text)
    if not candidates:
        raise ReplayScoringError(
            f"Trade idea '{decision_id}' invalidation has no numeric level",
            field="invalidation",
        )
    direct_stop = _first_number_after_stop_trigger(text)
    if direct_stop is not None:
        return direct_stop
    return _nearest_directional_level(
        candidates,
        direction=direction,
        entry_midpoint=entry_midpoint,
        level_role="stop",
    )


def _extract_target_level(
    text: str,
    decision_id: str,
    *,
    direction: TradeDirection,
    entry_midpoint: Decimal,
) -> Decimal:
    candidates = _price_level_numbers(text)
    if not candidates:
        raise ReplayScoringError(
            f"Trade idea '{decision_id}' target_exit has no numeric level",
            field="target_exit",
        )
    return _nearest_directional_level(
        candidates,
        direction=direction,
        entry_midpoint=entry_midpoint,
        level_role="target",
    )


def _nearest_directional_level(
    candidates: Sequence[Decimal],
    *,
    direction: TradeDirection,
    entry_midpoint: Decimal,
    level_role: str,
) -> Decimal:
    if direction is TradeDirection.LONG:
        directional_candidates = tuple(
            value
            for value in candidates
            if (value < entry_midpoint if level_role == "stop" else value > entry_midpoint)
        )
    elif direction is TradeDirection.SHORT:
        directional_candidates = tuple(
            value
            for value in candidates
            if (value > entry_midpoint if level_role == "stop" else value < entry_midpoint)
        )
    else:
        directional_candidates = tuple(candidates)

    if not directional_candidates:
        return candidates[0] if level_role == "target" else candidates[-1]
    return min(directional_candidates, key=lambda value: abs(value - entry_midpoint))


def _price_level_numbers(text: str) -> tuple[Decimal, ...]:
    values: list[Decimal] = []
    for match in _NUMBER_RE.finditer(text):
        if _is_non_price_quantity(text, match.end()):
            continue
        values.append(Decimal(match.group().replace(",", "")))
    return tuple(values)


def _first_number_after_stop_trigger(text: str) -> Decimal | None:
    match = _DIRECT_STOP_RE.search(text)
    if match is None or _is_non_price_quantity(text, match.end(1)):
        return None
    return Decimal(match.group(1).replace(",", ""))


def _is_non_price_quantity(text: str, number_end: int) -> bool:
    return _NON_PRICE_QUANTITY_SUFFIX_RE.match(text[number_end:]) is not None


def _validate_level_order(idea: TradeIdea, levels: ScoringLevels) -> None:
    entry = levels.entry_midpoint
    if idea.direction is TradeDirection.LONG:
        valid = levels.stop < entry < levels.target
    elif idea.direction is TradeDirection.SHORT:
        valid = levels.target < entry < levels.stop
    else:
        valid = True

    if not valid:
        raise ReplayScoringError(
            f"Trade idea '{idea.decision_id}' has inconsistent scoring levels",
            field="target_exit",
        )


def _first_entry_candle(
    candles: Sequence[Candle],
    levels: ScoringLevels,
) -> Candle | None:
    entry_price = levels.entry_midpoint
    for candle in candles:
        if candle.low <= entry_price <= candle.high:
            return candle
    return None


def _candle_ends_by_expiry(
    candle: Candle,
    *,
    expires_at: datetime,
    candle_duration: timedelta | None,
) -> bool:
    if candle_duration is None:
        return candle.ts < expires_at
    return candle.ts + candle_duration <= expires_at


def _entry_candle_proves_prefill_stop(
    direction: TradeDirection,
    candle: Candle,
    levels: ScoringLevels,
) -> bool:
    entry_price = levels.entry_midpoint
    if direction is TradeDirection.LONG:
        return candle.open == candle.low and candle.open <= levels.stop < entry_price
    if direction is TradeDirection.SHORT:
        return candle.open == candle.high and candle.open >= levels.stop > entry_price
    return False


def _bar_outcome_price(
    direction: TradeDirection,
    candle: Candle,
    levels: ScoringLevels,
    *,
    allow_favorable_exit: bool = True,
    allow_adverse_exit: bool = True,
) -> tuple[ReplayOutcome, Decimal] | None:
    # A single OHLC bar cannot reveal intrabar ordering. Score stops before
    # targets when both are touched in the same bar so calibration stays
    # conservative and never overstates replay quality.
    if direction is TradeDirection.LONG:
        if allow_adverse_exit and candle.low <= levels.stop:
            return ReplayOutcome.STOP_HIT, levels.stop
        if allow_favorable_exit and candle.high >= levels.target:
            return ReplayOutcome.TARGET_HIT, levels.target
        return None

    if allow_adverse_exit and candle.high >= levels.stop:
        return ReplayOutcome.STOP_HIT, levels.stop
    if allow_favorable_exit and candle.low <= levels.target:
        return ReplayOutcome.TARGET_HIT, levels.target
    return None


# Must stay in agreement with the recorder snapshot builder's granularity
# alias table, but the frozen trade_ideas dependency set (core, errors, itself)
# forbids importing it from gpt_trader.features.recorder; agreement is pinned
# by tests instead.
_GRANULARITY_DURATION_BY_ALIAS = {
    "1M": timedelta(minutes=1),
    "1MIN": timedelta(minutes=1),
    "1MINUTE": timedelta(minutes=1),
    "ONE_MINUTE": timedelta(minutes=1),
    "5M": timedelta(minutes=5),
    "5MIN": timedelta(minutes=5),
    "5MINUTE": timedelta(minutes=5),
    "FIVE_MINUTE": timedelta(minutes=5),
    "15M": timedelta(minutes=15),
    "15MIN": timedelta(minutes=15),
    "15MINUTE": timedelta(minutes=15),
    "FIFTEEN_MINUTE": timedelta(minutes=15),
    "30M": timedelta(minutes=30),
    "30MIN": timedelta(minutes=30),
    "30MINUTE": timedelta(minutes=30),
    "THIRTY_MINUTE": timedelta(minutes=30),
    "1H": timedelta(hours=1),
    "1HR": timedelta(hours=1),
    "1HOUR": timedelta(hours=1),
    "ONE_HOUR": timedelta(hours=1),
    "2H": timedelta(hours=2),
    "2HR": timedelta(hours=2),
    "2HOUR": timedelta(hours=2),
    "TWO_HOUR": timedelta(hours=2),
    "4H": timedelta(hours=4),
    "4HR": timedelta(hours=4),
    "4HOUR": timedelta(hours=4),
    "FOUR_HOUR": timedelta(hours=4),
    "6H": timedelta(hours=6),
    "6HR": timedelta(hours=6),
    "6HOUR": timedelta(hours=6),
    "SIX_HOUR": timedelta(hours=6),
    "1D": timedelta(days=1),
    "1DAY": timedelta(days=1),
    "ONE_DAY": timedelta(days=1),
}


def _granularity_duration(granularity: str) -> timedelta | None:
    normalized = granularity.strip().upper().replace("-", "_")
    return _GRANULARITY_DURATION_BY_ALIAS.get(normalized)


def _result(
    idea: TradeIdea,
    *,
    as_of: datetime,
    levels: ScoringLevels,
    outcome: ReplayOutcome,
    entry_time: datetime | None = None,
    entry_price: Decimal | None = None,
    exit_time: datetime | None = None,
    exit_price: Decimal | None = None,
    bars_evaluated: int = 0,
) -> ReplayResult:
    return_r = None
    return_pct = None
    if entry_price is not None and exit_price is not None:
        risk = (
            entry_price - levels.stop
            if idea.direction is TradeDirection.LONG
            else levels.stop - entry_price
        )
        if risk != 0:
            profit = (
                exit_price - entry_price
                if idea.direction is TradeDirection.LONG
                else entry_price - exit_price
            )
            return_r = profit / risk
        if entry_price != 0:
            signed_return = (
                exit_price - entry_price
                if idea.direction is TradeDirection.LONG
                else entry_price - exit_price
            )
            return_pct = signed_return / entry_price * Decimal("100")

    if idea.time_horizon.expires_at is None:
        raise ReplayScoringError(
            f"Trade idea '{idea.decision_id}' has no expiry to score against",
            field="time_horizon",
        )

    return ReplayResult(
        decision_id=idea.decision_id,
        record_hash=idea.record_hash(),
        instrument=idea.instrument,
        direction=idea.direction,
        as_of=as_of,
        expires_at=idea.time_horizon.expires_at,
        outcome=outcome,
        levels=levels,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        return_r=return_r,
        return_pct=return_pct,
        bars_evaluated=bars_evaluated,
        sized_notional=idea.sizing_recommendation.notional,
    )
