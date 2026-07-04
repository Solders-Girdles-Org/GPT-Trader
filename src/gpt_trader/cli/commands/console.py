"""Console command: launch the local operator web console.

Thin wrapper over ``gpt_trader.web`` (docs/decisions/adopt-operator-web-console.md).
The console renders trade-idea artifacts and forwards operator decisions to
identity-stamped TradeIdeaService calls; it never contacts a broker and has
no order-entry surface.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="cli")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "console",
        help="Serve the local operator web console (127.0.0.1 only)",
        description=(
            "Operator seat for the trade-idea review queue: pending ideas with "
            "eligibility and budget headroom, idea detail with audit trail, and "
            "approve / reject / request-changes decisions through the audited "
            "TradeIdeaService. Requires the 'web' extra (uv sync --extra web)."
        ),
    )
    parser.add_argument("--port", type=int, default=None, help="Local port (default: 8321)")
    parser.add_argument(
        "--ideas-root",
        type=Path,
        default=None,
        help="Trade-idea storage root (default: GPT_TRADER_IDEAS_ROOT or var/data/trade_ideas)",
    )
    parser.add_argument(
        "--actor",
        type=str,
        default=None,
        help="Operator identity stamped on decisions (default: GPT_TRADER_ACTOR or OS user)",
    )
    parser.set_defaults(handler=execute)


def execute(args: Namespace) -> int:
    try:
        from gpt_trader.web.__main__ import DEFAULT_CONSOLE_PORT, serve
    except ImportError:
        logger.error(
            "The operator console requires the 'web' extra: uv sync --extra web",
            operation="console",
        )
        return 1

    serve(
        port=args.port if args.port is not None else DEFAULT_CONSOLE_PORT,
        ideas_root=args.ideas_root,
        actor_id=args.actor,
    )
    return 0
