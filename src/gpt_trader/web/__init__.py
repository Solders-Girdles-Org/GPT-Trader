"""Operator web console: a thin, local-only adapter over TradeIdeaService.

Decided in docs/decisions/adopt-operator-web-console.md. This package renders
durable trade-idea artifacts and forwards operator decisions to the existing
identity-stamped service calls. It never imports live-trade internals and no
code path here constructs an order; the import-boundary guard enforces both.
"""

from gpt_trader.web.app import create_app

__all__ = ["create_app"]
