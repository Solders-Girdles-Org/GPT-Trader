from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from filelock import FileLock

from gpt_trader.features.trade_ideas import (
    DEFAULT_AUTONOMY_MODE,
    FAIL_CLOSED_AUTONOMY_MODE,
    ActorType,
    AutonomyIntegrityError,
    AutonomyMode,
    AutonomyStateEntry,
    AutonomyStateLog,
    autonomy_transition_violations,
    daily_loss_breach_evidence,
    drawdown_from_peak_breach_evidence,
    resolve_autonomy,
)
from gpt_trader.features.trade_ideas.autonomy import (
    AUTONOMY_SOURCE_FAIL_CLOSED,
    AUTONOMY_SOURCE_LOG,
    AUTONOMY_SOURCE_SEEDED_DEFAULT,
)


def build_entry(
    version: int = 1,
    mode: AutonomyMode = AutonomyMode.HUMAN_APPROVED_EXECUTION,
    actor_type: ActorType = ActorType.HUMAN,
    reason: str = "Recorded for test",
    evidence: tuple[str, ...] = (),
) -> AutonomyStateEntry:
    return AutonomyStateEntry(
        version=version,
        timestamp=datetime(2026, 7, 3, 9, version, tzinfo=UTC),
        mode=mode,
        actor_type=actor_type,
        actor_id="rj",
        reason=reason,
        evidence=evidence,
    )


@pytest.fixture
def autonomy_log(tmp_path: Path) -> AutonomyStateLog:
    return AutonomyStateLog(tmp_path / "autonomy_state.jsonl")


def test_seeded_default_is_human_approved_execution() -> None:
    assert DEFAULT_AUTONOMY_MODE is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert FAIL_CLOSED_AUTONOMY_MODE is AutonomyMode.RESEARCH_ONLY


def test_entry_round_trip() -> None:
    entry = build_entry(evidence=("breach detail",))

    restored = AutonomyStateEntry.from_dict(entry.to_dict())

    assert restored == entry


def test_entry_requires_rationale() -> None:
    with pytest.raises(ValueError, match="reason"):
        build_entry(reason="   ")


def test_entry_requires_positive_version() -> None:
    with pytest.raises(ValueError, match="version"):
        build_entry(version=0)


def test_empty_log_has_no_current_entry(autonomy_log: AutonomyStateLog) -> None:
    assert autonomy_log.current() is None
    assert autonomy_log.history() == []


def test_append_and_current(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.append(build_entry())

    current = autonomy_log.current()
    assert current is not None
    assert current.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert len(autonomy_log.history()) == 1


def test_versions_must_be_contiguous(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.append(build_entry())

    with pytest.raises(AutonomyIntegrityError):
        autonomy_log.append(build_entry(version=3, mode=AutonomyMode.BOUNDED_AUTONOMY))


def test_latest_entry_is_current(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.append(build_entry())
    autonomy_log.append(build_entry(version=2, mode=AutonomyMode.BOUNDED_AUTONOMY))

    current = autonomy_log.current()
    assert current is not None
    assert current.mode is AutonomyMode.BOUNDED_AUTONOMY
    assert [entry.version for entry in autonomy_log.history()] == [1, 2]


def test_malformed_line_raises_integrity_error(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.append(build_entry())
    with autonomy_log.path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    with pytest.raises(AutonomyIntegrityError, match="line 2 is malformed"):
        autonomy_log.history()


def test_version_gap_in_file_raises_integrity_error(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.append(build_entry())
    import json

    with autonomy_log.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(build_entry(version=5).to_dict()) + "\n")

    with pytest.raises(AutonomyIntegrityError, match="version 5; expected 2"):
        autonomy_log.history()


def test_invalid_utf8_raises_integrity_error(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.path.parent.mkdir(parents=True, exist_ok=True)
    autonomy_log.path.write_bytes(b"\xff\xfe not utf-8\n")

    with pytest.raises(AutonomyIntegrityError, match="unreadable"):
        autonomy_log.history()

    resolution = resolve_autonomy(autonomy_log)
    assert resolution.mode is FAIL_CLOSED_AUTONOMY_MODE


def test_duplicated_version_in_file_raises_integrity_error(
    autonomy_log: AutonomyStateLog,
) -> None:
    # The artifact an unlocked concurrent append would have left behind:
    # two entries both claiming the same next version.
    autonomy_log.append(build_entry())
    line = autonomy_log.path.read_text(encoding="utf-8")
    autonomy_log.path.write_text(line + line, encoding="utf-8")

    with pytest.raises(AutonomyIntegrityError, match="version 1; expected 2"):
        autonomy_log.history()

    with pytest.raises(AutonomyIntegrityError):
        autonomy_log.current()


def test_append_times_out_when_lock_is_held(
    autonomy_log: AutonomyStateLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AutonomyStateLog, "_LOCK_TIMEOUT_SECONDS", 0.05)
    autonomy_log.path.parent.mkdir(parents=True, exist_ok=True)
    foreign_lock = FileLock(str(autonomy_log.path) + ".lock")
    with foreign_lock.acquire(timeout=1):
        with pytest.raises(AutonomyIntegrityError, match="autonomy state log lock"):
            autonomy_log.append(build_entry())

    assert autonomy_log.current() is None


def test_unusable_lock_file_raises_integrity_error(autonomy_log: AutonomyStateLog) -> None:
    lock_path = Path(str(autonomy_log.path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.mkdir()

    with pytest.raises(AutonomyIntegrityError, match="lock is unusable"):
        autonomy_log.append(build_entry())

    assert autonomy_log.current() is None


def test_append_succeeds_after_lock_is_released(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.path.parent.mkdir(parents=True, exist_ok=True)
    foreign_lock = FileLock(str(autonomy_log.path) + ".lock")
    with foreign_lock.acquire(timeout=1):
        pass

    autonomy_log.append(build_entry())

    current = autonomy_log.current()
    assert current is not None
    assert current.version == 1


def test_resolve_absent_log_is_seeded_default(autonomy_log: AutonomyStateLog) -> None:
    resolution = resolve_autonomy(autonomy_log)

    assert resolution.mode is DEFAULT_AUTONOMY_MODE
    assert resolution.version is None
    assert resolution.source == AUTONOMY_SOURCE_SEEDED_DEFAULT
    assert resolution.error is None


def test_resolve_reads_latest_mode_from_log(autonomy_log: AutonomyStateLog) -> None:
    autonomy_log.append(build_entry())
    autonomy_log.append(build_entry(version=2, mode=AutonomyMode.BOUNDED_AUTONOMY))

    resolution = resolve_autonomy(autonomy_log)

    assert resolution.mode is AutonomyMode.BOUNDED_AUTONOMY
    assert resolution.version == 2
    assert resolution.source == AUTONOMY_SOURCE_LOG


def test_resolve_broken_log_fails_closed_to_research_only(
    autonomy_log: AutonomyStateLog,
) -> None:
    autonomy_log.path.parent.mkdir(parents=True, exist_ok=True)
    autonomy_log.path.write_text("garbage\n", encoding="utf-8")

    resolution = resolve_autonomy(autonomy_log)

    assert resolution.mode is AutonomyMode.RESEARCH_ONLY
    assert resolution.source == AUTONOMY_SOURCE_FAIL_CLOSED
    assert resolution.error is not None and "malformed" in resolution.error


@pytest.mark.parametrize(
    ("current_mode", "requested_mode"),
    [
        (AutonomyMode.HUMAN_APPROVED_EXECUTION, AutonomyMode.BOUNDED_AUTONOMY),
        (AutonomyMode.RESEARCH_ONLY, AutonomyMode.HUMAN_APPROVED_EXECUTION),
        (AutonomyMode.HUMAN_APPROVED_EXECUTION, AutonomyMode.HUMAN_APPROVED_EXECUTION),
    ],
)
@pytest.mark.parametrize("actor_type", [ActorType.AI, ActorType.SYSTEM, ActorType.VENUE])
def test_raising_or_reaffirming_requires_human(
    current_mode: AutonomyMode,
    requested_mode: AutonomyMode,
    actor_type: ActorType,
) -> None:
    violations = autonomy_transition_violations(
        current_mode=current_mode,
        requested_mode=requested_mode,
        actor_type=actor_type,
    )

    assert violations
    assert "requires a human actor" in violations[0]


@pytest.mark.parametrize(
    ("current_mode", "requested_mode"),
    [
        (AutonomyMode.BOUNDED_AUTONOMY, AutonomyMode.HUMAN_APPROVED_EXECUTION),
        (AutonomyMode.BOUNDED_AUTONOMY, AutonomyMode.RESEARCH_ONLY),
        (AutonomyMode.HUMAN_APPROVED_EXECUTION, AutonomyMode.RESEARCH_ONLY),
    ],
)
@pytest.mark.parametrize(
    "actor_type", [ActorType.AI, ActorType.SYSTEM, ActorType.VENUE, ActorType.HUMAN]
)
def test_lowering_is_open_to_any_actor(
    current_mode: AutonomyMode,
    requested_mode: AutonomyMode,
    actor_type: ActorType,
) -> None:
    violations = autonomy_transition_violations(
        current_mode=current_mode,
        requested_mode=requested_mode,
        actor_type=actor_type,
    )

    assert violations == []


def test_human_may_raise_the_level() -> None:
    violations = autonomy_transition_violations(
        current_mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
        requested_mode=AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
    )

    assert violations == []


def test_no_breach_evidence_at_or_below_the_daily_cap() -> None:
    moment = datetime(2026, 7, 3, 15, 0, tzinfo=UTC)

    assert (
        daily_loss_breach_evidence(
            same_day_realized_loss_pct=Decimal("10"),
            max_daily_loss_pct=Decimal("10"),
            budget_version=1,
            moment=moment,
        )
        is None
    )
    assert (
        daily_loss_breach_evidence(
            same_day_realized_loss_pct=Decimal("0"),
            max_daily_loss_pct=Decimal("10"),
            budget_version=1,
            moment=moment,
        )
        is None
    )


def test_breach_evidence_pins_appetite_source_and_trading_day() -> None:
    evidence = daily_loss_breach_evidence(
        same_day_realized_loss_pct=Decimal("12"),
        max_daily_loss_pct=Decimal("10"),
        budget_version=3,
        moment=datetime(2026, 7, 3, 23, 30, tzinfo=UTC),
    )

    assert evidence is not None
    assert "same_day_realized_loss_pct=12" in evidence[0]
    assert "max_daily_loss_pct=10" in evidence[0]
    assert "risk budget version 3" in evidence[0]
    assert "trading_day=2026-07-03" in evidence[0]


def test_drawdown_breach_evidence_requires_limit_and_measurement() -> None:
    moment = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    # No limit configured: nothing to breach.
    assert (
        drawdown_from_peak_breach_evidence(
            drawdown_from_peak_pct=Decimal("50"),
            max_drawdown_from_peak_pct=None,
            budget_version=1,
            high_water_mark=Decimal("1000"),
            current_equity=Decimal("500"),
            moment=moment,
        )
        is None
    )
    # No measurable drawdown (no attested basis yet): nothing to breach.
    assert (
        drawdown_from_peak_breach_evidence(
            drawdown_from_peak_pct=None,
            max_drawdown_from_peak_pct=Decimal("10"),
            budget_version=1,
            high_water_mark=None,
            current_equity=None,
            moment=moment,
        )
        is None
    )
    # Within appetite: no breach.
    assert (
        drawdown_from_peak_breach_evidence(
            drawdown_from_peak_pct=Decimal("10"),
            max_drawdown_from_peak_pct=Decimal("10"),
            budget_version=1,
            high_water_mark=Decimal("1000"),
            current_equity=Decimal("900"),
            moment=moment,
        )
        is None
    )


def test_drawdown_breach_evidence_pins_appetite_source_and_ledger_levels() -> None:
    evidence = drawdown_from_peak_breach_evidence(
        drawdown_from_peak_pct=Decimal("15"),
        max_drawdown_from_peak_pct=Decimal("10"),
        budget_version=4,
        high_water_mark=Decimal("2000"),
        current_equity=Decimal("1700"),
        moment=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )

    assert evidence is not None
    assert "drawdown_from_peak_pct=15" in evidence[0]
    assert "max_drawdown_from_peak_pct=10" in evidence[0]
    assert "version 4" in evidence[0]
    assert "high_water_mark=2000" in evidence[0]
    assert "current_equity=1700" in evidence[0]
