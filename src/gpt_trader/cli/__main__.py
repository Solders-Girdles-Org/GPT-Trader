"""
CLI entry point for the GPT-Trader application.

This module enables running the CLI via ``python -m gpt_trader.cli``.

Usage::

    # Via module
    python -m gpt_trader.cli [command] [options]

    # Via installed script
    gpt-trader [command] [options]

Commands
--------
Run ``gpt-trader --help`` for available commands including:

- ``live``: Start live trading session
- ``backtest``: Run strategy backtest
- ``preflight``: System health checks

Exit Codes
----------
- 0: Success
- 1: Runtime error
- 2: Configuration error
"""

from __future__ import annotations

import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())
