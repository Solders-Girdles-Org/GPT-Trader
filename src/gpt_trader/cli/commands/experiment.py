"""One operator entrypoint for isolated, recorded-data experiments."""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from gpt_trader.cli.options import add_output_options
from gpt_trader.cli.response import CliResponse
from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.held_out import build_input_payload, verify_input_payload
from gpt_trader.features.experiment.inputs import BENCHMARKS, load_input
from gpt_trader.features.experiment.ledger import inspect_run, run_input
from gpt_trader.features.experiment.reading import case_outcome, combined_action, rule_verdict


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "experiment", help="Run or inspect a broker-free recorded-data simulation"
    )
    commands = parser.add_subparsers(dest="experiment_command", required=True)
    for name in ("run", "inspect"):
        command = commands.add_parser(name)
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
                "--max-bars", type=int, help="Stop after this many additional committed bars"
            )
            command.add_argument(
                "--benchmark",
                choices=BENCHMARKS,
                help="Rule channel (default baseline); bound to the run and checked on resume",
            )
        add_output_options(command)
        command.set_defaults(handler=handle)
    fetch = commands.add_parser(
        "fetch-input",
        help="Download public Coinbase hourly candles (network, no credentials) into "
        "a retained raw capture plus one input per protocol rule and cost case",
    )
    fetch.add_argument("--protocol", type=Path, required=True)
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--out", type=Path, required=True, help="New, empty evidence directory")
    add_output_options(fetch)
    fetch.set_defaults(handler=handle_fetch)
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
    verify.set_defaults(handler=handle_verify)
    read = commands.add_parser("read", help="Apply the predeclared protocol reading to runs")
    read.add_argument("--protocol", type=Path, required=True)
    read.add_argument(
        "--runs",
        type=Path,
        required=True,
        help='JSON mapping {"<rule>": {"<cost case>": [experiment roots]}}',
    )
    add_output_options(read)
    read.set_defaults(handler=handle_read)


def handle(args: Namespace) -> CliResponse:
    if args.experiment_command == "inspect":
        result = inspect_run(args.root)
    else:
        result = run_experiment(
            args.root,
            load_input(args.input, benchmark=args.benchmark) if args.input else None,
            max_bars=args.max_bars,
            benchmark=args.benchmark,
        )
    account = result["account"]
    valuation = result["end_valuation"] or {}
    hold = (valuation.get("comparators") or {}).get("hold_the_coin") or {}
    text = "\n".join(
        [
            f"Recorded experiment: {result['processed_bars']}/{result['total_bars']} bars",
            f"Source: {result['source']}; {result['benchmark']} rule; no model or broker calls",
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


def handle_fetch(args: Namespace) -> CliResponse:
    from gpt_trader.features.recorder.candle_capture import capture_public_hourly_candles

    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    if args.symbol not in protocol["symbols"]:
        raise ValueError("Symbol is not in the protocol")
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("Fetch requires a new, empty evidence directory")
    capture = asyncio.run(
        capture_public_hourly_candles(
            args.symbol, aware_time(protocol["warmup_start"]), aware_time(protocol["cutoff"])
        )
    )
    # Validate every input before writing anything, so a gap leaves no evidence half-made.
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
        raise ValueError("Name at least one --input or --root to verify")
    capture = args.capture.read_bytes()
    checked = []
    for path in args.input:
        verify_input_payload(capture, json.loads(path.read_text(encoding="utf-8")))
        checked.append(str(path))
    for root in args.root:
        verify_input_payload(capture, run_input(root))
        checked.append(str(root))
    result = {"capture": str(args.capture), "verified": checked}
    return CliResponse.success_response(
        "experiment verify-input",
        data=result if args.output_format == "json" else json.dumps(result, indent=1),
    )


def handle_read(args: Namespace) -> CliResponse:
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    runs = json.loads(args.runs.read_text(encoding="utf-8"))
    if set(runs) != set(protocol["benchmarks"]) or any(
        set(cases) != set(protocol["cost_cases"]) for cases in runs.values()
    ):
        raise ValueError("Runs must name every protocol rule under every cost case")
    cases = {
        rule: {
            case: case_outcome(
                [inspect_run(Path(root)) for root in roots], protocol, rule=rule, case=case
            )
            for case, roots in runs[rule].items()
        }
        for rule in protocol["benchmarks"]
    }
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
