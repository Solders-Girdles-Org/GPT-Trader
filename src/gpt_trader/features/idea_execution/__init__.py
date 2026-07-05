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
  action lands on the append-only audit log with a system actor, under the
  dedicated ``paper`` audit venue.
- **At-most-once.** ``execute`` records the submission before touching the
  broker; a crash in between leaves the idea SUBMITTED, which admission
  refuses, so the same idea can never be placed twice.

Live order submission remains gated by docs/DIRECTION.md and recorded human
approval; nothing in this slice weakens that boundary.
"""

from gpt_trader.features.idea_execution.cycle import (
    DEFAULT_CYCLE_ACTOR_ID,
    ExecutionTurn,
    PaperCycleLockError,
    PaperCycleResult,
    PaperCycleRunner,
    ProposerTurn,
    SnapshotProvider,
)
from gpt_trader.features.idea_execution.executor import (
    AUTO_EXECUTION_ENV_VAR,
    DEFAULT_PAPER_EXECUTION_ACTOR_ID,
    PAPER_BROKER_TYPES,
    PAPER_EXECUTION_VENUE,
    IdeaNotExecutableError,
    PaperExecutionError,
    PaperExecutionResult,
    PaperIdeaExecutor,
    PaperOnlyLaneError,
    paper_auto_execution_gate_evidence,
    resolve_auto_execution_enabled,
)

__all__ = [
    "AUTO_EXECUTION_ENV_VAR",
    "DEFAULT_CYCLE_ACTOR_ID",
    "DEFAULT_PAPER_EXECUTION_ACTOR_ID",
    "PAPER_BROKER_TYPES",
    "PAPER_EXECUTION_VENUE",
    "IdeaNotExecutableError",
    "PaperExecutionError",
    "PaperExecutionResult",
    "ExecutionTurn",
    "PaperCycleLockError",
    "PaperCycleResult",
    "PaperCycleRunner",
    "PaperIdeaExecutor",
    "PaperOnlyLaneError",
    "ProposerTurn",
    "SnapshotProvider",
    "paper_auto_execution_gate_evidence",
    "resolve_auto_execution_enabled",
]
