"""One operator entrypoint for isolated, recorded-data experiments."""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import sqlite3
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from gpt_trader.cli.options import add_output_options
from gpt_trader.cli.response import CliErrorCode, CliResponse
from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.held_out import build_input_payload, verify_input_payload
from gpt_trader.features.experiment.inputs import (
    BENCHMARKS,
    MANAGER_CHANNEL,
    ExperimentInput,
    parse_input,
    with_benchmark,
)
from gpt_trader.features.experiment.ledger import (
    DATABASE,
    ExperimentIntegrityError,
    initial_state,
    inspect_run,
    run_input,
)
from gpt_trader.features.experiment.model_transport import (
    CREDENTIAL_ENV,
    SpendCeilingReached,
    SpendLedger,
    SpendLedgerError,
    TransportHalted,
    estimate_spend,
    from_environment,
    parse_cap,
    wire_params,
    worst_case_usd,
)
from gpt_trader.features.experiment.reading import case_outcome, combined_action, rule_verdict
from gpt_trader.features.experiment.trade_manager import (
    DecisionTransportProtocol,
    TransportUnavailableError,
    build_request,
)

T = TypeVar("T")
Handler = Callable[[Namespace], CliResponse]

FETCH_PROTOCOL_KEYS = (
    "symbols",
    "warmup_start",
    "cutoff",
    "scored_from",
    "settings",
    "benchmarks",
    "cost_cases",
)
READ_PROTOCOL_KEYS = (
    "protocol",
    "symbols",
    "scored_from",
    "cutoff",
    "bars_per_symbol",
    "settings",
    "benchmarks",
    "cost_cases",
    "min_scored_symbols",
    "min_round_trips",
)
PROTOCOL_SHAPES: dict[str, type] = {
    "symbols": list,
    "benchmarks": list,
    "settings": dict,
    "cost_cases": dict,
}
CAPTURE_KEYS = ("schema", "granularity", "start", "end", "requests")


class ExperimentCliError(Exception):
    """An operator mistake reported as a typed envelope, without a traceback."""

    def __init__(self, code: CliErrorCode, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _reported(handler: Handler) -> Handler:
    """Return expected operator mistakes as typed errors; real faults still reach main."""

    @functools.wraps(handler)
    def guarded(args: Namespace) -> CliResponse:
        try:
            return handler(args)
        except ExperimentCliError as error:
            return CliResponse.error_response(
                f"experiment {args.experiment_command}",
                code=error.code,
                message=str(error),
                details=error.details,
            )

    return guarded


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise ExperimentCliError(
            CliErrorCode.FILE_NOT_FOUND, f"{label} {path} does not exist", path=str(path)
        ) from None
    except OSError as error:
        raise ExperimentCliError(
            CliErrorCode.INVALID_ARGUMENT,
            f"Could not read {label.lower()} {path}: {error.strerror or error}",
            path=str(path),
        ) from None


def _parse_json(raw: bytes, path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID, f"{label} {path} is not UTF-8 text", path=str(path)
        ) from None
    except json.JSONDecodeError as error:
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID,
            f"{label} {path} is not valid JSON: {error.msg} "
            f"(line {error.lineno}, column {error.colno})",
            path=str(path),
        ) from None
    if not isinstance(payload, dict):
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID, f"{label} {path} must be a JSON object", path=str(path)
        )
    return payload


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _parse_json(_read_bytes(path, label), path, label)


def _read_protocol(path: Path, required: tuple[str, ...]) -> dict[str, Any]:
    protocol = _read_json(path, "Protocol")
    missing = [key for key in required if key not in protocol]
    if missing:
        names = ", ".join(repr(key) for key in missing)
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID,
            f"Protocol {path} is missing required key{'s' if len(missing) > 1 else ''} {names}",
            path=str(path),
            fields=missing,
        )
    misshapen = [
        key
        for key in required
        if key in PROTOCOL_SHAPES and not isinstance(protocol[key], PROTOCOL_SHAPES[key])
    ]
    if "cost_cases" in required and "cost_cases" not in misshapen:
        if not all(isinstance(costs, dict) for costs in protocol["cost_cases"].values()):
            misshapen.append("cost_cases")
    if misshapen:
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID,
            f"Protocol {path} has a malformed {', '.join(repr(key) for key in misshapen)}",
            path=str(path),
            fields=misshapen,
        )
    return protocol


def _read_capture(path: Path) -> bytes:
    capture = _read_bytes(path, "Capture")
    payload = _parse_json(capture, path, "Capture")
    missing = [key for key in CAPTURE_KEYS if key not in payload]
    if missing or not isinstance(payload["requests"], list):
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID,
            f"Capture {path} is not a candle capture"
            + (f"; missing {', '.join(repr(key) for key in missing)}" if missing else ""),
            path=str(path),
        )
    return capture


def _load_input(path: Path, benchmark: str | None) -> ExperimentInput:
    payload = _read_json(path, "Input")
    try:
        return parse_input(with_benchmark(payload, benchmark))
    except (ValueError, TypeError, ArithmeticError) as error:
        raise ExperimentCliError(
            CliErrorCode.VALIDATION_ERROR, f"Input {path} is invalid: {error}", path=str(path)
        ) from None


def _existing_run(root: Path, hint: str = "") -> Path:
    if not (root / DATABASE).is_file():
        raise ExperimentCliError(
            CliErrorCode.RUN_NOT_FOUND,
            f"Experiment root {root} has no {DATABASE}{hint}",
            root=str(root),
        )
    return root


def _on_root(root: Path, operation: Callable[[], T]) -> T:
    """Type ledger refusals and unusable files; leave engine faults to main's traceback."""
    try:
        return operation()
    except sqlite3.DatabaseError as error:
        # Integrity/Programming errors are code faults; only a bad or busy file is reported.
        bad_file = type(error) is sqlite3.DatabaseError
        if not bad_file and not isinstance(error, sqlite3.OperationalError):
            raise
        raise ExperimentCliError(
            CliErrorCode.OPERATION_FAILED,
            f"Experiment root {root}: could not use {DATABASE}: {error}",
            root=str(root),
        ) from None
    except ExperimentIntegrityError as error:
        raise ExperimentCliError(
            CliErrorCode.VALIDATION_ERROR, f"Experiment root {root}: {error}", root=str(root)
        ) from None
    except TransportUnavailableError as error:
        raise ExperimentCliError(
            CliErrorCode.OPERATION_FAILED, f"Experiment root {root}: {error}", root=str(root)
        ) from None
    except (SpendCeilingReached, TransportHalted) as error:
        raise ExperimentCliError(
            CliErrorCode.OPERATION_FAILED,
            f"Experiment root {root}: run stopped: {error}. The bar it was deciding was not "
            "committed; earlier bars are kept",
            root=str(root),
        ) from None
    except OSError as error:
        raise ExperimentCliError(
            CliErrorCode.INVALID_ARGUMENT,
            f"Experiment root {root} is unusable: {error.strerror or error}",
            root=str(root),
        ) from None


def _inspect(root: Path) -> dict[str, Any]:
    _existing_run(root)
    return _on_root(root, lambda: inspect_run(root))


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "experiment", help="Run or inspect a broker-free recorded-data simulation"
    )
    commands = parser.add_subparsers(dest="experiment_command", required=True)
    for name, summary in (
        ("run", "Create or resume a recorded experiment"),
        ("inspect", "Show a recorded experiment's state without changing it"),
    ):
        command = commands.add_parser(name, help=summary)
        command.add_argument(
            "--root", type=Path, required=True, help="Isolated experiment directory"
        )
        if name == "run":
            command.add_argument(
                "--input",
                type=Path,
                help="Required for a new run; bound inputs are retained for resume",
            )
            command.add_argument(
                "--max-bars",
                type=_positive_int,
                help="Stop after this many additional committed bars",
            )
            command.add_argument(
                "--benchmark",
                choices=BENCHMARKS,
                help="Decision channel; defaults to the input file's channel, else baseline. Must "
                "not contradict a channel the file names; bound to the run and checked on "
                "resume. ai-trade-manager runs only with --model-transport",
            )
            command.add_argument(
                "--model-transport",
                choices=("anthropic",),
                help="Opt in to paid model calls for the ai-trade-manager channel, with the "
                f"credential from {CREDENTIAL_ENV}; requires --spend-ledger and --spend-cap-usd",
            )
            command.add_argument(
                "--spend-ledger",
                type=Path,
                help="Spend ledger file outside the run, shared by every run under one cap; "
                "created on first use and bound to its cap",
            )
            command.add_argument(
                "--spend-cap-usd",
                help="Hard spend cap in USD; a call that could pass it is never sent",
            )
        add_output_options(command)
        command.set_defaults(handler=_reported(handle))
    fetch = commands.add_parser(
        "fetch-input",
        help="Download public Coinbase hourly candles (network, no credentials) into "
        "a retained raw capture plus one input per protocol rule and cost case",
    )
    fetch.add_argument("--protocol", type=Path, required=True)
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--out", type=Path, required=True, help="New, empty evidence directory")
    add_output_options(fetch)
    fetch.set_defaults(handler=_reported(handle_fetch))
    estimate = commands.add_parser(
        "estimate-spend",
        help="Measure an ai-trade-manager input's model requests offline and bound their "
        "cost (no credential, network or model call)",
    )
    estimate.add_argument("--input", type=Path, required=True)
    add_output_options(estimate)
    estimate.set_defaults(handler=_reported(handle_estimate))
    verify = commands.add_parser(
        "verify-input",
        help="Rebuild inputs from their raw capture and refuse any difference",
    )
    verify.add_argument("--capture", type=Path, required=True, help="Retained capture.json")
    verify.add_argument(
        "--input", type=Path, action="append", default=[], help="Input file (repeatable)"
    )
    verify.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="Experiment run whose bound input is checked (repeatable)",
    )
    add_output_options(verify)
    verify.set_defaults(handler=_reported(handle_verify))
    read = commands.add_parser("read", help="Apply the predeclared protocol reading to runs")
    read.add_argument("--protocol", type=Path, required=True)
    read.add_argument(
        "--runs",
        type=Path,
        required=True,
        help='JSON mapping {"<rule>": {"<cost case>": [experiment roots]}}',
    )
    add_output_options(read)
    read.set_defaults(handler=_reported(handle_read))


def handle(args: Namespace) -> CliResponse:
    root = args.root
    if args.experiment_command == "inspect":
        result = _inspect(root)
    else:
        source = _load_input(args.input, args.benchmark) if args.input else None
        if source is None:
            _existing_run(root, "; create it with --input")
            channel = _on_root(root, lambda: run_input(root))["benchmark"]
        else:
            channel = source.benchmark
        if channel == MANAGER_CHANNEL and args.model_transport is None:
            # Model calls cost money; refuse before creating or advancing anything.
            raise ExperimentCliError(
                CliErrorCode.OPERATION_FAILED,
                f"The {MANAGER_CHANNEL} channel needs a model transport; paid model calls run "
                "only with --model-transport anthropic, --spend-ledger and --spend-cap-usd. "
                "Nothing was created or advanced",
                root=str(root),
            )
        if channel != MANAGER_CHANNEL and args.model_transport is not None:
            raise ExperimentCliError(
                CliErrorCode.INVALID_ARGUMENT,
                f"--model-transport applies only to the {MANAGER_CHANNEL} channel",
                root=str(root),
            )
        if source is not None and root.exists() and not root.is_dir():
            raise ExperimentCliError(
                CliErrorCode.INVALID_ARGUMENT,
                f"Experiment root {root} is not a directory",
                root=str(root),
            )
        elif (
            source is not None
            and not (root / DATABASE).exists()
            and root.exists()
            and any(root.iterdir())
        ):
            raise ExperimentCliError(
                CliErrorCode.INVALID_ARGUMENT,
                f"New experiment requires an empty directory; {root} is not empty",
                root=str(root),
            )
        transport = None
        if args.model_transport is not None:
            bound = source or _on_root(root, lambda: parse_input(run_input(root)))
            assert bound.manager is not None
            transport = _model_transport(args, root, bound)
        result = _on_root(
            root,
            lambda: run_experiment(
                root,
                source,
                max_bars=args.max_bars,
                benchmark=args.benchmark,
                transport=transport,
            ),
        )
    account = result["account"]
    valuation = result["end_valuation"] or {}
    hold = (valuation.get("comparators") or {}).get("hold_the_coin") or {}
    text = "\n".join(
        [
            f"Recorded experiment: {result['processed_bars']}/{result['total_bars']} bars",
            f"Source: {result['source']}; {result['benchmark']} rule; no model or broker calls"
            if "manager" not in result
            else f"Source: {result['source']}; {result['benchmark']} channel; "
            f"{result['manager']['turns']} journaled model turns; no broker calls",
            f"Cash ${account['cash']}; position {account['quantity']} {result['symbol']}; equity ${account['equity']}",
            f"Net P&L ${account['net_pnl']} (realized ${account['realized_net']}, unrealized ${account['unrealized_net']}); fees ${account['fees']}",
            f"At liquidation value: net ${valuation.get('net_pnl')}; hold-the-coin ${hold.get('net_pnl')}; cash $0",
            f"Independent reconciliation passed; {account['fill_count']} simulated fills",
            f"Last decision: {result['last_decision']}",
            "This is simulation evidence, not trading performance or readiness.",
            f"Decision counts: {json.dumps(result['decision_counts'], sort_keys=True)}",
        ]
    )
    return CliResponse.success_response(
        f"experiment {args.experiment_command}",
        data=result if args.output_format == "json" else text,
    )


def _model_transport(
    args: Namespace, root: Path, source: ExperimentInput
) -> DecisionTransportProtocol:
    """Check the spend ledger and build the real transport; neither creates a file."""
    assert source.manager is not None
    missing = [
        flag
        for flag, value in (
            ("--spend-ledger", args.spend_ledger),
            ("--spend-cap-usd", args.spend_cap_usd),
        )
        if value is None
    ]
    if missing:
        raise ExperimentCliError(
            CliErrorCode.MISSING_ARGUMENT,
            f"--model-transport requires {' and '.join(missing)}; no model call is made without "
            "a hard spend cap",
        )
    ledger_path = args.spend_ledger.resolve()
    if ledger_path == root.resolve() or root.resolve() in ledger_path.parents:
        raise ExperimentCliError(
            CliErrorCode.INVALID_ARGUMENT,
            f"Spend ledger {args.spend_ledger} must be outside the experiment root {root}",
            path=str(args.spend_ledger),
        )
    if not os.environ.get(CREDENTIAL_ENV):
        raise ExperimentCliError(
            CliErrorCode.MISSING_ARGUMENT,
            f"--model-transport anthropic reads its credential from {CREDENTIAL_ENV}, which is "
            "not set; nothing was created or advanced",
        )
    try:
        # Neither creates a file: the ledger is written by its first reservation.
        ledger = SpendLedger(args.spend_ledger, parse_cap(args.spend_cap_usd))
        transport = from_environment(source.manager, ledger)
        # A new run's first turn is exact (no fill can precede it); a resume's next
        # turn is near it, and its real reservation is still checked at the call.
        first = build_request(source, source.scored_index, initial_state(source))
        least = worst_case_usd(wire_params(first, source.manager))
        if ledger.spent_usd() + least > ledger.cap_usd:
            raise ExperimentCliError(
                CliErrorCode.OPERATION_FAILED,
                f"Spend ledger {args.spend_ledger} has ${ledger.spent_usd()} of its "
                f"${ledger.cap_usd} cap charged, too little room for one call (up to "
                f"${least}); nothing was created or advanced",
                path=str(args.spend_ledger),
            )
        return transport
    except SpendLedgerError as error:
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID, str(error), path=str(args.spend_ledger)
        ) from None
    except TransportHalted as error:
        raise ExperimentCliError(CliErrorCode.OPERATION_FAILED, str(error)) from None
    except (sqlite3.DatabaseError, OSError) as error:
        raise ExperimentCliError(
            CliErrorCode.OPERATION_FAILED,
            f"Could not use spend ledger {args.spend_ledger}: {error}",
            path=str(args.spend_ledger),
        ) from None


def handle_estimate(args: Namespace) -> CliResponse:
    source = _load_input(args.input, None)
    if source.manager is None:
        raise ExperimentCliError(
            CliErrorCode.INVALID_ARGUMENT,
            f"Input {args.input} is not an {MANAGER_CHANNEL} input",
            path=str(args.input),
        )
    try:
        result = estimate_spend(source)
    except TransportHalted as error:
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID, f"Input {args.input}: {error}", path=str(args.input)
        ) from None
    return CliResponse.success_response(
        "experiment estimate-spend",
        data=result if args.output_format == "json" else json.dumps(result, indent=1),
    )


def handle_fetch(args: Namespace) -> CliResponse:
    from gpt_trader.features.recorder.candle_capture import capture_public_hourly_candles

    protocol = _read_protocol(args.protocol, FETCH_PROTOCOL_KEYS)
    if args.symbol not in protocol["symbols"]:
        raise ExperimentCliError(
            CliErrorCode.INVALID_ARGUMENT,
            f"Symbol {args.symbol} is not in protocol {args.protocol}",
            symbol=args.symbol,
        )
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        raise ExperimentCliError(
            CliErrorCode.INVALID_ARGUMENT,
            f"Fetch requires a new, empty evidence directory; {args.out} is not one",
            path=str(args.out),
        )
    try:
        start, end = aware_time(protocol["warmup_start"]), aware_time(protocol["cutoff"])
    except (ValueError, TypeError) as error:
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID,
            f"Protocol {args.protocol} has an invalid warmup_start or cutoff: {error}",
            path=str(args.protocol),
        ) from None
    capture = asyncio.run(capture_public_hourly_candles(args.symbol, start, end))
    # Validate every input before writing anything, so a gap leaves no evidence half-made.
    try:
        inputs = {
            f"{rule}-{case}.json": build_input_payload(
                capture,
                scored_from=protocol["scored_from"],
                benchmark=rule,
                settings={**protocol["settings"], **costs},
            )
            for rule in protocol["benchmarks"]
            for case, costs in protocol["cost_cases"].items()
        }
    except ValueError as error:
        raise ExperimentCliError(
            CliErrorCode.VALIDATION_ERROR,
            f"Fetched {args.symbol} candles cannot build protocol inputs; nothing written: {error}",
            symbol=args.symbol,
        ) from None
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "capture.json").write_bytes(capture)
    for name, payload in inputs.items():
        (args.out / name).write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    result = {
        "out": str(args.out),
        "inputs": sorted(inputs),
        "source": inputs[next(iter(inputs))]["source"],
    }
    return CliResponse.success_response(
        "experiment fetch-input",
        data=result if args.output_format == "json" else json.dumps(result, indent=1),
    )


def handle_verify(args: Namespace) -> CliResponse:
    if not args.input and not args.root:
        raise ExperimentCliError(
            CliErrorCode.MISSING_ARGUMENT, "Name at least one --input or --root to verify"
        )
    capture = _read_capture(args.capture)
    payloads = [(str(path), _read_json(path, "Input")) for path in args.input]
    for root in args.root:
        _existing_run(root)
        payloads.append((str(root), _on_root(root, functools.partial(run_input, root))))
    checked = []
    for name, payload in payloads:
        try:
            verify_input_payload(capture, payload)
        except ValueError as error:
            raise ExperimentCliError(
                CliErrorCode.VALIDATION_ERROR,
                f"{name} does not verify against capture {args.capture}: {error}",
                path=name,
            ) from None
        checked.append(name)
    result = {"capture": str(args.capture), "verified": checked}
    return CliResponse.success_response(
        "experiment verify-input",
        data=result if args.output_format == "json" else json.dumps(result, indent=1),
    )


def handle_read(args: Namespace) -> CliResponse:
    protocol = _read_protocol(args.protocol, READ_PROTOCOL_KEYS)
    if not {"baseline", "mean-reversion"} <= set(protocol["benchmarks"]) or not {
        "base",
        "stress",
    } <= set(protocol["cost_cases"]):
        raise ExperimentCliError(
            CliErrorCode.CONFIG_INVALID,
            f"Protocol {args.protocol} must name rules baseline and mean-reversion "
            "and cost cases base and stress",
            path=str(args.protocol),
        )
    runs = _read_json(args.runs, "Runs file")
    if set(runs) != set(protocol["benchmarks"]) or any(
        not isinstance(cases, dict)
        or set(cases) != set(protocol["cost_cases"])
        or any(
            not isinstance(roots, list) or not all(isinstance(root, str) for root in roots)
            for roots in cases.values()
        )
        for cases in runs.values()
    ):
        raise ExperimentCliError(
            CliErrorCode.VALIDATION_ERROR,
            f"Runs file {args.runs} must map every protocol rule to every cost case, "
            "each a list of experiment roots",
            path=str(args.runs),
        )
    try:
        cases = {
            rule: {
                case: case_outcome(
                    [_inspect(Path(root)) for root in roots], protocol, rule=rule, case=case
                )
                for case, roots in runs[rule].items()
            }
            for rule in protocol["benchmarks"]
        }
    except ValueError as error:
        raise ExperimentCliError(
            CliErrorCode.VALIDATION_ERROR,
            f"Runs file {args.runs} does not fit protocol {args.protocol}: {error}",
            path=str(args.runs),
        ) from None
    verdicts = {
        rule: rule_verdict(cases[rule]["base"]["outcome"], cases[rule]["stress"]["outcome"])
        for rule in cases
    }
    result = {
        "protocol": protocol["protocol"],
        "cases": cases,
        "verdicts": verdicts,
        "action": combined_action(verdicts["mean-reversion"], verdicts["baseline"]),
    }
    return CliResponse.success_response(
        "experiment read",
        data=result if args.output_format == "json" else json.dumps(result, indent=1),
    )
