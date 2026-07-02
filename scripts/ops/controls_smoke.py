#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import signal
from argparse import ArgumentParser
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from gpt_trader.app.config import BotConfig, BotRiskConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.execution.submission_result import OrderSubmissionStatus
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

OUTPUT_DIR = Path("var/ops")
EXIT_SUCCESS = 0
EXIT_GUARD_BLOCKED = 1
EXIT_RUNTIME_FAILURE = 2


@dataclass
class SmokeResult:
    name: str
    status: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class ExitOutcome:
    label: str
    exit_code: int


@dataclass(frozen=True)
class SmokeSummary:
    severity_counts: dict[str, int]
    top_failing_checks: list[dict[str, Any]]
    total_failing_checks: int
    max_displayed_failures: int

    @property
    def truncated(self) -> bool:
        return self.total_failing_checks > self.max_displayed_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity_counts": self.severity_counts,
            "top_failing_checks": self.top_failing_checks,
            "total_failing_checks": self.total_failing_checks,
            "max_displayed_failures": self.max_displayed_failures,
            "truncated": self.truncated,
        }


SUMMARY_SCHEMA_VERSION = "1.0"
DEFAULT_SUMMARY_MAX_FAILURES = 3
_SUMMARY_FAILURE_PRIORITY = {"fail": 0, "warn": 1}

SUMMARY_TOP_LEVEL_KEYS = (
    "timestamp",
    "outcome",
    "exit_code",
    "summary_version",
    "source",
    "summary",
)
SUMMARY_SUMMARY_KEYS = (
    "severity_counts",
    "top_failing_checks",
    "total_failing_checks",
    "max_displayed_failures",
    "truncated",
)
SUMMARY_SEVERITY_KEYS = ("pass", "warn", "fail")
SUMMARY_FAILURE_ENTRY_KEYS = ("name", "severity", "detail")


@dataclass(frozen=True)
class ControlsSmokeCliArgs:
    dry_run_summary: bool
    max_summary_failures: int


class SignalInterrupt(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(f"signal {signum}")
        self.signum = signum


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _install_signal_handlers() -> None:
    def _handle_signal(signum: int, _: Any) -> None:
        raise SignalInterrupt(signum)

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), _handle_signal)


def _build_engine() -> TradingEngine:
    risk = BotRiskConfig(position_fraction=Decimal("0.1"))
    config = BotConfig(symbols=["BTC-USD"], interval=1, risk=risk)
    config.profile = "dev"
    container = ApplicationContainer(config)
    set_application_container(container)
    risk_manager = MagicMock()
    risk_manager.check_mark_staleness.return_value = False
    risk_manager.is_reduce_only_mode.return_value = False
    risk_manager.config = MagicMock()
    risk_manager.config.kill_switch_enabled = False
    risk_manager.check_order.return_value = True
    risk_manager._daily_pnl_triggered = False

    broker = MagicMock()
    broker.get_market_snapshot.return_value = {"spread_bps": 5, "depth_l1": 10000}
    broker.list_balances.return_value = []
    broker.list_positions.return_value = []

    context = CoordinatorContext(
        config=config,
        broker=broker,
        risk_manager=risk_manager,
        event_store=container.event_store,
        orders_store=container.orders_store,
        container=container,
    )
    engine = TradingEngine(context)

    engine._state_collector = MagicMock()
    engine._state_collector.require_product.return_value = MagicMock()
    engine._state_collector.resolve_effective_price.return_value = Decimal("50000")
    engine._state_collector.build_positions_dict.return_value = {}

    engine._guard_manager = MagicMock()
    engine._guard_manager.run_runtime_guards.return_value = None
    engine._guard_manager._api_error_window = []
    engine._guard_manager._api_error_window_seconds = 60
    engine._guard_manager._api_error_rate_threshold = 0.5
    engine._guard_manager._api_rate_limit_usage_threshold = 0.9

    engine._order_validator = MagicMock()
    engine._order_validator.validate_exchange_rules.return_value = (Decimal("1"), None)
    engine._order_validator.enforce_slippage_guard.return_value = None
    engine._order_validator.ensure_mark_is_fresh.return_value = None
    engine._order_validator.run_pre_trade_validation.return_value = None
    engine._order_validator.maybe_preview_order.return_value = None
    engine._order_validator.finalize_reduce_only_flag.return_value = False
    engine._order_validator.enable_order_preview = True

    failure_tracker = MagicMock()
    failure_tracker.get_failure_count.return_value = 0

    security_result = MagicMock()
    security_result.is_valid = True
    security_result.errors = []
    engine._order_validator.validate_security_constraints.return_value = security_result

    engine._order_submitter = MagicMock()
    engine._order_submitter.submit_order.return_value = "order-1"
    engine._order_submitter.generate_client_order_id.return_value = "decision-1"
    return engine


def _run_kill_switch(engine: TradingEngine) -> SmokeResult:
    risk_manager = cast(MagicMock, engine.context.risk_manager)
    risk_manager.config.kill_switch_enabled = True
    decision = Decision(Action.BUY, "ops controls smoke")

    result = asyncio.run(
        engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=decision,
            price=Decimal("50000"),
            equity=Decimal("10000"),
            quantity_override=Decimal("1"),
        )
    )

    detail = {
        "status": result.status.value,
        "reason": str(result.reason) if result.reason is not None else None,
        "decision_id": result.decision_trace.decision_id if result.decision_trace else None,
    }
    if (
        result.status == OrderSubmissionStatus.BLOCKED
        and result.reason == "kill_switch"
        and detail["decision_id"]
    ):
        return SmokeResult("kill_switch", "passed", detail)
    return SmokeResult("kill_switch", "failed", detail)


def _run_reduce_only_blocks_entry(engine: TradingEngine) -> SmokeResult:
    risk_manager = cast(MagicMock, engine.context.risk_manager)
    risk_manager.config.kill_switch_enabled = False
    risk_manager.is_reduce_only_mode.return_value = True
    risk_manager.check_order.return_value = False
    risk_manager._daily_pnl_triggered = False
    engine._current_positions = {}

    decision = Decision(Action.BUY, "ops controls smoke")
    result = asyncio.run(
        engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=decision,
            price=Decimal("50000"),
            equity=Decimal("10000"),
            quantity_override=Decimal("1"),
        )
    )

    detail = {
        "status": result.status.value,
        "reason": str(result.reason) if result.reason is not None else None,
    }
    if result.status == OrderSubmissionStatus.BLOCKED:
        return SmokeResult("reduce_only_block", "passed", detail)
    return SmokeResult("reduce_only_block", "failed", detail)


def _ensure_serializable(engine: TradingEngine) -> None:
    order_submitter = cast(MagicMock, engine._order_submitter)

    def _record_decision_trace(*_: Any, **__: Any) -> None:
        return None

    def _record_preview(*_: Any, **__: Any) -> None:
        return None

    def _record_rejection(*_: Any, **__: Any) -> None:
        return None

    order_submitter.record_decision_trace.side_effect = _record_decision_trace
    order_submitter.record_preview.side_effect = _record_preview
    order_submitter.record_rejection.side_effect = _record_rejection
    order_submitter.record_decision_trace.return_value = None
    order_submitter.record_preview.return_value = None
    order_submitter.record_rejection.return_value = None


def _run_reduce_only_allows_exit(engine: TradingEngine) -> SmokeResult:
    risk_manager = cast(MagicMock, engine.context.risk_manager)
    risk_manager.config.kill_switch_enabled = False
    risk_manager.is_reduce_only_mode.return_value = True
    risk_manager.check_order.return_value = True
    risk_manager._daily_pnl_triggered = False
    risk_manager.check_mark_staleness.return_value = False
    engine._current_positions = {"BTC-USD": MagicMock(quantity=Decimal("1"), side="long")}

    order_validator = cast(MagicMock, engine._order_validator)
    order_validator.validate_exchange_rules.return_value = (Decimal("0.1"), None)
    order_validator.enforce_slippage_guard.return_value = None
    order_validator.ensure_mark_is_fresh.return_value = None
    order_validator.run_pre_trade_validation.return_value = None
    order_validator.maybe_preview_order.return_value = None
    order_validator.finalize_reduce_only_flag.return_value = False

    decision = Decision(Action.SELL, "ops controls smoke")
    result = asyncio.run(
        engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=decision,
            price=Decimal("50000"),
            equity=Decimal("10000"),
            quantity_override=Decimal("1"),
        )
    )

    detail = {
        "status": result.status.value,
        "order_id": str(result.order_id) if result.order_id is not None else None,
    }
    if result.status == OrderSubmissionStatus.SUCCESS:
        return SmokeResult("reduce_only_exit", "passed", detail)
    return SmokeResult("reduce_only_exit", "failed", detail)


def run_smoke_checks() -> list[SmokeResult]:
    with (
        patch(
            "gpt_trader.features.live_trade.engines.strategy.create_strategy",
            return_value=MagicMock(),
        ),
        patch(
            "gpt_trader.security.validate.get_validator",
        ) as validator,
    ):
        engine = _build_engine()
        security_result = MagicMock()
        security_result.is_valid = True
        security_result.errors = []
        validator.return_value.validate_order_request.return_value = security_result
        _ensure_serializable(engine)

    async def _bypass_order_guards(
        *_: Any,
        price: Decimal,
        quantity: Decimal,
        reduce_only_flag: bool,
        **__: Any,
    ) -> tuple[Decimal, Decimal, bool, None]:
        return quantity, price, reduce_only_flag, None

    with (
        patch(
            "gpt_trader.features.live_trade.engines.strategy.TradingEngine._run_security_validation",
            return_value=None,
        ),
        patch(
            "gpt_trader.features.live_trade.engines.strategy.TradingEngine._run_order_validator_guards",
            new=_bypass_order_guards,
        ),
    ):
        return [
            _run_kill_switch(engine),
            _run_reduce_only_blocks_entry(engine),
            _run_reduce_only_allows_exit(engine),
        ]


def is_guard_blocked(result: SmokeResult) -> bool:
    return result.detail.get("status") == OrderSubmissionStatus.BLOCKED.value


def determine_outcome(results: list[SmokeResult]) -> ExitOutcome:
    if not results:
        return ExitOutcome("runtime_failure", EXIT_RUNTIME_FAILURE)
    if all(result.status == "passed" for result in results):
        return ExitOutcome("success", EXIT_SUCCESS)
    failed_results = [result for result in results if result.status != "passed"]
    if failed_results and all(is_guard_blocked(result) for result in failed_results):
        return ExitOutcome("guard_blocked", EXIT_GUARD_BLOCKED)
    return ExitOutcome("runtime_failure", EXIT_RUNTIME_FAILURE)


def _classify_severity(result: SmokeResult) -> str:
    if result.status == "passed":
        return "pass"
    if is_guard_blocked(result):
        return "warn"
    return "fail"


def summarize_smoke_results(
    results: list[SmokeResult],
    max_top_failures: int = DEFAULT_SUMMARY_MAX_FAILURES,
) -> SmokeSummary:
    severity_counts = {"pass": 0, "warn": 0, "fail": 0}
    failures: list[dict[str, Any]] = []
    for result in results:
        severity = _classify_severity(result)
        severity_counts[severity] += 1

        if severity != "pass":
            failures.append(
                {
                    "name": result.name,
                    "severity": severity,
                    "detail": result.detail,
                }
            )

    sorted_failures = sorted(
        failures,
        key=lambda payload: (
            _SUMMARY_FAILURE_PRIORITY.get(payload["severity"], 0),
            payload["name"],
        ),
    )
    displayed = max(0, max_top_failures)
    top_failures = sorted_failures[:displayed] if displayed > 0 else []

    return SmokeSummary(
        severity_counts=severity_counts,
        top_failing_checks=top_failures,
        total_failing_checks=len(failures),
        max_displayed_failures=displayed,
    )


def validate_summary_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("summary payload must be a dict")

    missing_top = [key for key in SUMMARY_TOP_LEVEL_KEYS if key not in payload]
    if missing_top:
        raise ValueError(f"missing summary keys: {missing_top}")

    summary = payload["summary"]
    if not isinstance(summary, dict):
        raise ValueError("summary field must be a dict")

    missing_summary = [key for key in SUMMARY_SUMMARY_KEYS if key not in summary]
    if missing_summary:
        raise ValueError(f"summary missing keys: {missing_summary}")

    severity_counts = summary["severity_counts"]
    if not isinstance(severity_counts, dict):
        raise ValueError("severity_counts must be a dict")

    missing_severity = [key for key in SUMMARY_SEVERITY_KEYS if key not in severity_counts]
    if missing_severity:
        raise ValueError(f"severity_counts missing keys: {missing_severity}")

    top_failures = summary["top_failing_checks"]
    if not isinstance(top_failures, list):
        raise ValueError("top_failing_checks must be a list")

    for entry in top_failures:
        if not isinstance(entry, dict):
            raise ValueError("each failing check entry must be a dict")
        missing_entry_keys = [key for key in SUMMARY_FAILURE_ENTRY_KEYS if key not in entry]
        if missing_entry_keys:
            raise ValueError(f"failing check entry missing keys: {missing_entry_keys}")

    total = summary["total_failing_checks"]
    if not isinstance(total, int):
        raise ValueError("total_failing_checks must be an int")

    max_displayed = summary["max_displayed_failures"]
    if not isinstance(max_displayed, int):
        raise ValueError("max_displayed_failures must be an int")

    truncated = summary["truncated"]
    if not isinstance(truncated, bool):
        raise ValueError("truncated must be a bool")


def build_summary_payload(
    results: list[SmokeResult],
    outcome: ExitOutcome,
    max_top_failures: int = DEFAULT_SUMMARY_MAX_FAILURES,
) -> dict[str, Any]:
    summary = summarize_smoke_results(results, max_top_failures=max_top_failures)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome.label,
        "exit_code": outcome.exit_code,
        "summary_version": SUMMARY_SCHEMA_VERSION,
        "source": "controls_smoke",
        "summary": summary.to_dict(),
    }
    validate_summary_payload(payload)
    return payload


def parse_args(argv: Sequence[str] | None = None) -> ControlsSmokeCliArgs:
    parser = ArgumentParser(description="Run controls smoke guard checks")
    parser.add_argument(
        "--dry-run-summary",
        action="store_true",
        help="Print a deterministic summary (pass/warn/fail counts) without persisting artifacts",
    )
    parser.add_argument(
        "--max-summary-failures",
        type=int,
        default=DEFAULT_SUMMARY_MAX_FAILURES,
        help=f"Maximum number of failing checks to include in the summary (default: {DEFAULT_SUMMARY_MAX_FAILURES})",
    )
    parsed = parser.parse_args(argv)
    return ControlsSmokeCliArgs(
        dry_run_summary=bool(parsed.dry_run_summary),
        max_summary_failures=max(0, parsed.max_summary_failures or 0),
    )


def _format_signal_error(signum: int) -> str:
    try:
        signal_name = signal.Signals(signum).name.lower()
    except ValueError:
        signal_name = str(signum)
    return f"signal_{signal_name}"


def _emit_status(outcome: ExitOutcome, output_path: Path | None, error: str | None) -> None:
    if output_path is not None:
        print(f"output_path={output_path}")
    print(f"outcome={outcome.label}")
    print(f"exit_code={outcome.exit_code}")
    if error:
        print(f"error={error}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary_mode = args.dry_run_summary
    _install_signal_handlers()
    outcome = ExitOutcome("runtime_failure", EXIT_RUNTIME_FAILURE)
    output_path: Path | None = None
    error: str | None = None
    try:
        results = run_smoke_checks()
        outcome = determine_outcome(results)
        if summary_mode:
            summary_payload = build_summary_payload(
                results, outcome, max_top_failures=args.max_summary_failures
            )
            print(json.dumps(summary_payload, indent=2, default=str))
        else:
            output = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "outcome": outcome.label,
                "exit_code": outcome.exit_code,
                "results": [
                    {"name": result.name, "status": result.status, "detail": result.detail}
                    for result in results
                ],
            }

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / f"controls_smoke_{_utc_timestamp()}.json"
            output_path.write_text(json.dumps(output, indent=2))
    except SignalInterrupt as exc:
        outcome = ExitOutcome("runtime_failure", EXIT_RUNTIME_FAILURE)
        error = _format_signal_error(exc.signum)
    except KeyboardInterrupt:
        outcome = ExitOutcome("runtime_failure", EXIT_RUNTIME_FAILURE)
        error = "keyboard_interrupt"
    except Exception as exc:  # noqa: BLE001 - compact CLI error signal
        outcome = ExitOutcome("runtime_failure", EXIT_RUNTIME_FAILURE)
        error = str(exc) or exc.__class__.__name__
    finally:
        if not summary_mode or error:
            _emit_status(outcome, output_path, error)
        clear_application_container()
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
