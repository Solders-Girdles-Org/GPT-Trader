"""One operator entrypoint for isolated, recorded-data experiments.

This module is the experiment's composition root: it injects the selectable
deterministic rules, so the experiment slice never imports runtime strategy
code. Mean reversion is the existing ``MeanReversionStrategy`` (default
config, no tuning) through the snapshot-strategy wrapper, the same recipe as
``gpt-trader ideas propose-strategy --strategy mean-reversion``.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from gpt_trader.cli.options import add_output_options
from gpt_trader.cli.response import CliResponse
from gpt_trader.features.experiment.engine import (
    DEFAULT_BENCHMARKS,
    BenchmarkCatalog,
    BenchmarkProposer,
    run_experiment,
)
from gpt_trader.features.experiment.inputs import BENCHMARK_HISTORY_BARS, parse_input
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError, inspect_run


def _mean_reversion_proposer() -> BenchmarkProposer:
    from gpt_trader.app.config import MeanReversionConfig
    from gpt_trader.features.live_trade.strategies.mean_reversion import MeanReversionStrategy
    from gpt_trader.features.strategy_tools import SnapshotStrategyProposer

    # Final-bar drive: mean reversion decides from the mark window alone, and
    # its cooldown stays inert on the wrapper's flat book.
    return SnapshotStrategyProposer(
        lambda: MeanReversionStrategy(MeanReversionConfig()),
        strategy_name="mean-reversion",
        drive="final-bar",
    )


def experiment_benchmarks() -> BenchmarkCatalog:
    """Every rule `experiment run` can select, by bound benchmark name."""
    return {**DEFAULT_BENCHMARKS, "mean-reversion": _mean_reversion_proposer}


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
                choices=sorted(BENCHMARK_HISTORY_BARS),
                help="Rule for a new run (default baseline); bound to the run's identity",
            )
        add_output_options(command)
        command.set_defaults(handler=handle)


def handle(args: Namespace) -> CliResponse:
    benchmarks = experiment_benchmarks()
    if args.experiment_command == "inspect":
        result = inspect_run(args.root, benchmarks)
    else:
        source = None
        if args.input:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if args.benchmark:
                if payload.get("benchmark", args.benchmark) != args.benchmark:
                    raise ValueError("--benchmark conflicts with the benchmark in the input file")
                payload["benchmark"] = args.benchmark
            source = parse_input(payload)
        elif args.benchmark:
            bound = inspect_run(args.root, benchmarks)["benchmark"]
            if bound != args.benchmark:
                raise ExperimentIntegrityError(
                    f"Run is bound to benchmark {bound}; use a new experiment directory"
                )
        result = run_experiment(args.root, source, max_bars=args.max_bars, proposers=benchmarks)
    account = result["account"]
    window = result["window"]
    valuation = result["end_valuation"]
    hold = result["comparators"]["hold_the_coin"]
    text = "\n".join(
        [
            f"Recorded experiment: {result['processed_bars']}/{result['total_bars']} bars",
            f"Benchmark: {result['benchmark']}; source: {result['source']}; no model or broker calls",
            f"Window: {window['warmup_bars']} warm-up bars (history only), "
            f"{window['scored_bars']} scored bars from {window['scored_from']}",
            f"Cash ${account['cash']}; position {account['quantity']} {result['symbol']}; equity ${account['equity']}",
            f"Net P&L ${account['net_pnl']} (realized ${account['realized_net']}, unrealized ${account['unrealized_net']}); fees ${account['fees']}",
            f"Independent reconciliation passed; {account['fill_count']} simulated fills, "
            f"{result['round_trips']} completed round trips",
            (
                f"End valuation (after exit costs): net ${valuation['net_pnl']}"
                + ("; position still open" if valuation["position_open"] else "")
                if valuation
                else "End valuation: no bars processed"
            ),
            (
                f"Hold the coin: net ${hold['net_pnl']} (price moved {hold['price_move_fraction']})"
                if hold
                else "Hold the coin: first scored bar not processed yet"
            ),
            "Cash: net $0",
            f"Last decision: {result['last_decision']}",
            "This is simulation evidence, not trading performance or readiness.",
            f"Decision counts: {json.dumps(result['decision_counts'], sort_keys=True)}",
        ]
    )
    return CliResponse.success_response(
        f"experiment {args.experiment_command}",
        data=result if args.output_format == "json" else text,
    )
