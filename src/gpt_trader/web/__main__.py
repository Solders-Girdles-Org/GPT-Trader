"""Run the operator console: ``python -m gpt_trader.web``.

The bind host is hard-coded to 127.0.0.1 by decision
(docs/decisions/adopt-operator-web-console.md): the console is the local
owner's seat, never a network service.
"""

from __future__ import annotations

import argparse
from pathlib import Path

CONSOLE_HOST = "127.0.0.1"
DEFAULT_CONSOLE_PORT = 8321


def serve(
    *,
    port: int = DEFAULT_CONSOLE_PORT,
    ideas_root: Path | None = None,
    actor_id: str | None = None,
) -> None:
    import uvicorn

    from gpt_trader.web.app import create_app

    app = create_app(ideas_root=ideas_root, actor_id=actor_id)
    uvicorn.run(app, host=CONSOLE_HOST, port=port, log_level="info")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_CONSOLE_PORT, help="Local port to bind")
    parser.add_argument(
        "--ideas-root", type=Path, default=None, help="Trade-idea storage root override"
    )
    parser.add_argument(
        "--actor", type=str, default=None, help="Operator identity stamped on decisions"
    )
    args = parser.parse_args(argv)
    serve(port=args.port, ideas_root=args.ideas_root, actor_id=args.actor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
