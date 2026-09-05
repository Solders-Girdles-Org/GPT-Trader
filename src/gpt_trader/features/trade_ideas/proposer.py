"""Proposer protocol: snapshot in, complete trade-idea records out.

Deterministic baselines and model-backed implementations share this contract;
they do not necessarily share determinism or a valid performance evaluation.
Historical snapshots support mechanical and fixture checks. Performance claims
for model-generated proposals require prospective evidence under
``docs/decisions/adopt-agentic-alpha-direction.md``; replaying a historical
window cannot establish uncontaminated trading skill.

Implementations may receive additional context through injected dependencies.
They never submit orders: their output enters the workflow through
``TradeIdeaService.propose`` and remains subject to its admission controls.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from gpt_trader.features.trade_ideas.models import TradeIdea
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot


@runtime_checkable
class Proposer(Protocol):
    """Generates eligible trade-idea records from a point-in-time snapshot."""

    @property
    def proposer_id(self) -> str:
        """Stable actor identifier recorded on every proposed idea."""
        ...

    def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
        """Return zero or more complete, eligibility-passing records."""
        ...
