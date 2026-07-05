"""Strategy-eligibility gate for trade ideas, split into two constraint classes.

Implements the eligibility split from
docs/decisions/adopt-event-driven-execution-topology.md (#1190):

- **Invariant constraints** (this module's checks) apply identically at every
  autonomy level. They are blast-radius controls from the accepted framework
  (docs/DIRECTION.md, Decision 2): ideas without an invalidation level,
  max-loss estimate, reproducible data source, defined entry, exit rule, or
  expiry are never actionable, no matter who or what is approving.
- **Mode-dependent constraints** (review-latency survivability, enforced by
  ``ApprovalPolicy.review_latency_violation``) exist only because a human
  review loop is in the decision path. They apply under
  ``human_approved_execution`` (and the fail-closed ``research_only``); under
  ``bounded_autonomy`` the horizon floor comes from measured capability, not
  human latency.

Rejection reasons carry their class prefix so the audit trail can distinguish
"unsound idea" from "too fast for a human" when building track-record
evidence.
"""

from __future__ import annotations

from gpt_trader.features.trade_ideas.models import TradeIdea

INVARIANT_ELIGIBILITY_PREFIX = "invariant eligibility: "
MODE_DEPENDENT_ELIGIBILITY_PREFIX = "mode-dependent eligibility (human_approved_execution): "


def evaluate_eligibility(idea: TradeIdea) -> list[str]:
    """Return invariant rejection reasons; an empty list means eligible.

    Every check here is autonomy-level invariant. Mode-dependent constraints
    never belong in this function — they live on ``ApprovalPolicy``, which
    knows the resolved autonomy mode.
    """
    reasons: list[str] = []

    if not idea.thesis.strip():
        reasons.append("Missing thesis: no plain-language reason the trade exists")
    if not idea.instrument.strip():
        reasons.append("Missing instrument: no exact symbol or product identifier")
    if not idea.invalidation.strip():
        reasons.append("Missing invalidation: no level or condition that makes the thesis false")
    if not idea.target_exit.strip():
        reasons.append("Missing target_exit: no target, time stop, or exit condition")
    if idea.max_loss.amount is None and idea.max_loss.percent_of_account is None:
        reasons.append("Missing max_loss: no dollar or percent loss estimate")
    if not idea.data_used:
        reasons.append("Missing data_used: no reproducible data sources recorded")
    if idea.time_horizon.expires_at is None:
        reasons.append("Missing expiry: no review deadline or expiration time")
    entry = idea.entry_zone
    if entry.lower is None and entry.upper is None and not entry.trigger.strip():
        reasons.append("Missing entry_zone: no price range or conditional trigger")
    if not idea.failure_mode.strip():
        reasons.append("Missing failure_mode: most likely way the trade fails is not recorded")

    return reasons


def is_eligible(idea: TradeIdea) -> bool:
    """True when the idea survives every invariant rejection condition."""
    return not evaluate_eligibility(idea)
