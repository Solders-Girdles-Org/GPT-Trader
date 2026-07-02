from __future__ import annotations

import os
from collections import Counter
from decimal import InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas import (
    AuditIntegrityError,
    RiskBudgetLog,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaStore,
    resolve_ideas_root,
)

if TYPE_CHECKING:
    from gpt_trader.features.trade_ideas.audit import AuditEvent
    from gpt_trader.preflight.core import PreflightCheck


def _details(ideas_root: Path, *, audit_path: Path, budget_path: Path) -> dict[str, str]:
    return {
        "ideas_root": str(ideas_root),
        "audit_path": str(audit_path),
        "budget_path": str(budget_path),
    }


def _root_access_error(ideas_root: Path) -> str | None:
    if not ideas_root.exists():
        return f"Trade ideas root missing: {ideas_root}"
    if not ideas_root.is_dir():
        return f"Trade ideas root is not a directory: {ideas_root}"
    if not os.access(ideas_root, os.W_OK | os.X_OK):
        return f"Trade ideas root is not writable: {ideas_root}"
    return None


def _existing_log_append_error(path: Path, *, label: str) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        return f"Trade ideas {label} is not a file: {path}"
    if not os.access(path, os.W_OK):
        return f"Trade ideas {label} is not appendable: {path}"
    return None


def _latest_state_counts(events: list[AuditEvent]) -> Counter[TradeIdeaState]:
    latest_states: dict[str, TradeIdeaState] = {}
    for event in events:
        latest_states[event.decision_id] = event.after_state
    return Counter(latest_states.values())


def _validate_budget_history(budget_log: RiskBudgetLog) -> tuple[int, int] | None:
    entries = budget_log.history()
    if not entries:
        return None
    for expected_version, entry in enumerate(entries, start=1):
        if entry.budget.version != expected_version:
            raise ValidationError(
                (
                    "Risk budget versions must be contiguous; "
                    f"expected {expected_version}, got {entry.budget.version}"
                ),
                field="version",
                value=entry.budget.version,
            )
    return len(entries), entries[-1].budget.version


def _validate_audit_record_references(
    service: TradeIdeaService,
    events: list[AuditEvent],
) -> None:
    for event in events:
        service.load_record_version(event.decision_id, event.record_hash)


def _orphaned_decision_ids(ideas_root: Path, events: list[AuditEvent]) -> list[str]:
    """Return stored decision ids that no audit event references.

    The service treats a persisted record without an audit trail as corrupt
    (``get()``/``list_view_result()`` raise ``AuditIntegrityError``), so the
    readiness check must fail on the same state instead of reporting READY.
    """
    audited = {event.decision_id for event in events}
    store = TradeIdeaStore(ideas_root / "records")
    return sorted(
        decision_id for decision_id in store.list_decision_ids() if decision_id not in audited
    )


def _validate_latest_records(
    service: TradeIdeaService,
    events: list[AuditEvent],
) -> None:
    """Load every audited decision through the service's own read gate.

    Audited version files can be intact while ``latest.json`` was tampered
    with, replaced, or deleted; iterating the audited decision ids (not the
    store listing, which skips directories without ``latest.json``) and
    calling ``service.get()`` re-verifies exactly what the read/approve paths
    will do, so READY matches reality.
    """
    for decision_id in sorted({event.decision_id for event in events}):
        view = service.get(decision_id)
        if view.idea.decision_id != decision_id:
            raise AuditIntegrityError(
                f"Stored trade idea '{decision_id}' latest record contains "
                f"decision_id '{view.idea.decision_id}'",
                field="decision_id",
                value=view.idea.decision_id,
            )


def _check_cli_surface(checker: PreflightCheck, details: dict[str, str]) -> bool:
    try:
        from gpt_trader.cli.commands.ideas import register as register_ideas_cli
    except Exception as exc:
        checker.log_error(f"Trade ideas CLI surface unavailable: {exc}", details=details)
        return False

    if not callable(register_ideas_cli):
        checker.log_error("Trade ideas CLI surface unavailable: register is not callable", details)
        return False

    checker.log_success("Trade ideas CLI/service surfaces reachable", details=details)
    return True


def check_trade_ideas_readiness(checker: PreflightCheck) -> bool:
    """Validate read-only trade-idea readiness for approval-gated review sessions."""
    checker.section_header("14. TRADE IDEAS READINESS")

    ideas_root = resolve_ideas_root().expanduser()
    service = TradeIdeaService(ideas_root)
    audit_path = service.audit_log.path
    budget_path = ideas_root / "risk_budget.jsonl"
    details = _details(ideas_root, audit_path=audit_path, budget_path=budget_path)
    all_good = True

    root_error = _root_access_error(ideas_root)
    if root_error is None:
        checker.log_success(f"Trade ideas root writable: {ideas_root}", details=details)
    else:
        checker.log_error(root_error, details=details)
        all_good = False

    for append_error in (
        _existing_log_append_error(audit_path, label="audit log"),
        _existing_log_append_error(budget_path, label="risk budget log"),
    ):
        if append_error is not None:
            checker.log_error(append_error, details=details)
            all_good = False

    if not _check_cli_surface(checker, details):
        all_good = False

    try:
        events = service.audit_log.verify()
    except AuditIntegrityError as exc:
        checker.log_error(f"Trade ideas audit integrity failed: {exc}", details=details)
        all_good = False
    except UnicodeDecodeError as exc:
        checker.log_error(f"Trade ideas audit unreadable: {exc}", details=details)
        all_good = False
    except OSError as exc:
        checker.log_error(f"Trade ideas audit unreadable: {exc}", details=details)
        all_good = False
    else:
        try:
            _validate_audit_record_references(service, events)
        except AuditIntegrityError as exc:
            checker.log_error(f"Trade ideas audit record integrity failed: {exc}", details=details)
            all_good = False
        except OSError as exc:
            checker.log_error(f"Trade ideas audit records unreadable: {exc}", details=details)
            all_good = False
        else:
            orphaned = _orphaned_decision_ids(ideas_root, events)
            if orphaned:
                checker.log_error(
                    "Trade ideas records missing audit trail: " + ", ".join(orphaned),
                    details={**details, "orphaned_decision_ids": ", ".join(orphaned)},
                )
                all_good = False
            try:
                _validate_latest_records(service, events)
            except (AuditIntegrityError, ValidationError) as exc:
                checker.log_error(
                    f"Trade ideas latest record integrity failed: {exc}", details=details
                )
                all_good = False
            except OSError as exc:
                checker.log_error(f"Trade ideas records unreadable: {exc}", details=details)
                all_good = False
            event_details = {**details, "event_count": len(events)}
            checker.log_success(
                f"Trade ideas audit verified at {audit_path}: {len(events)} event(s)",
                details=event_details,
            )
            pending_proposed = _latest_state_counts(events)[TradeIdeaState.PROPOSED]
            if pending_proposed:
                checker.log_warning(
                    f"Trade ideas pending review: {pending_proposed} proposed idea(s)",
                    details={**event_details, "pending_proposed_count": pending_proposed},
                )

    budget_log = RiskBudgetLog(budget_path)
    try:
        budget_summary = _validate_budget_history(budget_log)
    except (ValidationError, InvalidOperation, KeyError, TypeError, ValueError, OSError) as exc:
        checker.log_error(f"Trade ideas risk budget unreadable: {exc}", details=details)
        all_good = False
    else:
        if budget_summary is None:
            checker.log_error(f"Trade ideas risk budget not seeded: {budget_path}", details=details)
            all_good = False
        else:
            entry_count, current_version = budget_summary
            checker.log_success(
                f"Trade ideas risk budget current at {budget_path}: version {current_version}",
                details={
                    **details,
                    "budget_entry_count": entry_count,
                    "budget_version": current_version,
                },
            )

    return all_good


__all__ = ["check_trade_ideas_readiness"]
