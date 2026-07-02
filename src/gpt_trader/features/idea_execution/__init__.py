"""Paper execution lane for APPROVED trade ideas.

First component of the accepted five-role composition
(docs/decisions/adopt-five-role-composition.md): the executor arm that
consumes ideas from the approval-gated workflow in
``gpt_trader.features.trade_ideas`` and executes them against a paper broker.

Lane contract (enforced in code, not convention):

- **Paper-only.** The lane accepts exactly the paper/mock broker types in
  ``PAPER_BROKER_TYPES``; anything else — including duck-typed lookalikes and
  subclasses — is rejected at construction with ``PaperOnlyLaneError``. There
  is no configuration path that routes a live broker into this slice.
- **APPROVED ideas only.** Ideas in any other workflow state, or past their
  ``expires_at``, are refused with ``IdeaNotExecutableError``.
- Lifecycle facts are recorded only through ``TradeIdeaService`` so every
  action lands on the append-only audit log with a system actor.

Live order submission remains gated by docs/DIRECTION.md and recorded human
approval; nothing in this slice weakens that boundary.
"""

from gpt_trader.features.idea_execution.executor import (
    PAPER_BROKER_TYPES,
    IdeaNotExecutableError,
    PaperIdeaExecutor,
    PaperOnlyLaneError,
)

__all__ = [
    "PAPER_BROKER_TYPES",
    "IdeaNotExecutableError",
    "PaperIdeaExecutor",
    "PaperOnlyLaneError",
]
