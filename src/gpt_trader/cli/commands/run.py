"""Run command for the trading bot CLI."""

from __future__ import annotations

import asyncio
import inspect
import os
import signal
from argparse import Namespace
from types import FrameType
from typing import Any

from gpt_trader.app.config.validation import ConfigValidationError
from gpt_trader.app.container import clear_application_container, get_application_container
from gpt_trader.cli import options, services
from gpt_trader.config.constants import OTEL_ENABLED, OTEL_EXPORTER_ENDPOINT, OTEL_SERVICE_NAME
from gpt_trader.monitoring.tracing import init_tracing, shutdown_tracing
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="cli")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "run",
        help="Run the trading loop",
        description="Perpetuals Trading Bot",
    )
    options.add_profile_option(parser, allow_missing_default=True)
    options.add_runtime_options(parser)
    parser.add_argument(
        "--config",
        type=str,
        metavar="PATH",
        help="Path to YAML config file (supports nested strategy/risk sections)",
    )
    parser.add_argument("--dev-fast", action="store_true", help="Run single cycle and exit")
    parser.set_defaults(handler=execute)


def execute(args: Namespace) -> int:
    try:
        config = services.build_config_from_args(
            args,
            include=options.RUNTIME_CONFIG_KEYS,
            skip={"dev_fast"},
        )

        # Auto-reduce interval for fast development iteration
        if args.dev_fast and getattr(args, "interval", None) is None:
            config.interval = 1  # 1 second instead of default 60

        # Initialize OpenTelemetry tracing if enabled
        if OTEL_ENABLED:
            init_tracing(
                service_name=OTEL_SERVICE_NAME,
                endpoint=OTEL_EXPORTER_ENDPOINT,
                enabled=True,
            )

        bot = services.instantiate_bot(config)
    except ConfigValidationError as exc:
        message = str(exc)
        if "symbols overrides" in message:
            logger.error("Symbols must be non-empty")
        else:
            logger.error(message)
        return 1

    return _run_bot(bot, single_cycle=args.dev_fast)


def _run_bot(bot: Any, *, single_cycle: bool) -> int:
    def signal_handler(sig: int, frame: FrameType | None) -> None:  # pragma: no cover - signal
        logger.info(f"Signal {sig} received, shutting down...", operation="shutdown")
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    async def run_bot() -> None:
        health_server = None
        health_state = None

        if _env_flag("GPT_TRADER_HEALTH_SERVER_ENABLED"):
            try:
                from gpt_trader.app.health_server import (
                    DEFAULT_HEALTH_PORT,
                    mark_live,
                    start_health_server,
                )

                container = get_application_container()
                health_state = container.health_state if container is not None else None
                port = _env_int("GPT_TRADER_HEALTH_PORT", DEFAULT_HEALTH_PORT)
                health_server = await start_health_server(port=port, health_state=health_state)
                if health_state is not None:
                    mark_live(health_state, True, reason="cli_run")
            except Exception as exc:
                logger.warning(
                    "Failed to start health server",
                    operation="health_server_start",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

        try:
            await bot.run(single_cycle=single_cycle)
        finally:
            if health_state is not None:
                from gpt_trader.app.health_server import mark_live, mark_ready

                # If the bot stops, report unhealthy readiness/liveness.
                mark_ready(health_state, False, reason="shutdown")
                mark_live(health_state, False, reason="shutdown")

            if health_server is not None:
                try:
                    await health_server.stop()
                except Exception:
                    pass

    try:
        run_coro = run_bot()
        try:
            asyncio.run(run_coro)
        finally:
            # Avoid "coroutine was never awaited" warnings when asyncio.run is mocked.
            if (
                inspect.iscoroutine(run_coro)
                and inspect.getcoroutinestate(run_coro) == inspect.CORO_CREATED
            ):
                run_coro.close()
    except KeyboardInterrupt:  # pragma: no cover - defensive
        logger.info("Shutdown complete.", status="stopped")
    finally:
        # Flush any pending trace spans
        shutdown_tracing()
        # Clear container registry to prevent leaks between runs
        clear_application_container()

    return 0


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
