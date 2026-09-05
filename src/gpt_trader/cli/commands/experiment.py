"""One operator entrypoint for isolated, recorded-data experiments."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from gpt_trader.cli.options import add_output_options
from gpt_trader.cli.response import CliResponse
from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.inputs import load_input
from gpt_trader.features.experiment.ledger import inspect_run


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
        add_output_options(command)
        command.set_defaults(handler=handle)


def handle(args: Namespace) -> CliResponse:
    if args.experiment_command == "inspect":
        result = inspect_run(args.root)
    else:
        result = run_experiment(
            args.root, load_input(args.input) if args.input else None, max_bars=args.max_bars
        )
    account = result["account"]
    text = "\n".join(
        [
            f"Recorded experiment: {result['processed_bars']}/{result['total_bars']} bars",
            f"Source: {result['source']}; rule-based benchmark; no model or broker calls",
            f"Cash ${account['cash']}; position {account['quantity']} {result['symbol']}; equity ${account['equity']}",
            f"Net P&L ${account['net_pnl']} (realized ${account['realized_net']}, unrealized ${account['unrealized_net']}); fees ${account['fees']}",
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
