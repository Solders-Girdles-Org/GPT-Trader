"""Record command: standalone market-data recording without the trading engine."""

from __future__ import annotations

import asyncio
import signal
from argparse import Namespace
from types import FrameType
from typing import Any

from gpt_trader.app.config.validation import ConfigValidationError
from gpt_trader.cli import options, services
from gpt_trader.features.recorder import (
    MarketDataRecorder,
    MarketDataRecorderConfig,
    PriceTickStore,
    derive_recorder_bot_id,
)
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="cli")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "record",
        help="Record market data (price ticks) without running the trading engine",
        description=(
            "Standalone recorder role: poll read-only tickers for the profile's "
            "symbols and persist price ticks to the EventStore, so observation "
            "keeps running while execution is halted or paused. Never reads "
            "accounts or places, modifies, or cancels orders."
        ),
    )
    options.add_profile_option(parser, allow_missing_default=True)
    parser.add_argument(
        "--symbols",
        type=str,
        nargs="+",
        help="Symbols to record (default: the profile's symbols)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="Poll interval in seconds (default: the profile's interval)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Record a single poll and exit (for verification)",
    )
    parser.set_defaults(handler=execute)


def execute(args: Namespace) -> int:
    try:
        config = services.build_config_from_args(
            args,
            include={"symbols", "interval"},
        )
        container = services.instantiate_container(config)
    except ConfigValidationError as exc:
        logger.error(str(exc))
        return 1

    broker = container.broker
    if broker is None:
        logger.error("No broker available for market-data reads")
        return 1

    bot_id = derive_recorder_bot_id(config.profile)
    tick_store = PriceTickStore(
        event_store=container.event_store,
        symbols=list(config.symbols),
        bot_id=bot_id,
    )
    recorder = MarketDataRecorder(
        broker=broker,
        tick_store=tick_store,
        config=MarketDataRecorderConfig(
            symbols=tuple(config.symbols),
            interval_seconds=float(config.interval),
        ),
    )

    if args.once:
        recorded = asyncio.run(recorder.record_once())
        logger.info(
            "Recorded one poll",
            recorded_ticks=recorded,
            symbols=list(config.symbols),
            bot_id=bot_id,
            operation="record",
        )
        return 0 if recorded > 0 else 1

    def signal_handler(sig: int, frame: FrameType | None) -> None:  # pragma: no cover - signal
        logger.info(f"Signal {sig} received, stopping recorder...", operation="record")
        recorder.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    asyncio.run(recorder.run())
    return 0
