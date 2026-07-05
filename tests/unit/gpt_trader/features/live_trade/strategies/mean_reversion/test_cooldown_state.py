"""Caller-owned cooldown state for MeanReversionStrategy (#1164 stage 3).

Default per-instance cooldown behavior is pinned by
test_strategy_exit_logic_and_cooldown.py; these tests pin the externalized
surface: an injected ``CooldownState`` is the only cooldown truth, so a caller
that owns the state can observe it, share it across instances, or hand a
fresh one to a replay window.
"""

from __future__ import annotations

from decimal import Decimal

from gpt_trader.app.config import MeanReversionConfig
from gpt_trader.features.live_trade.strategies.baseline import Action
from gpt_trader.features.live_trade.strategies.mean_reversion import (
    CooldownState,
    MeanReversionStrategy,
)

FLAT_MARKS = [Decimal("100")] * 20
DIP_MARKS = [Decimal("100")] * 19 + [Decimal("85")]
LONG_POSITION = {
    "quantity": Decimal("0.1"),
    "side": "long",
    "entry_price": Decimal("95"),
}


def _config() -> MeanReversionConfig:
    return MeanReversionConfig(
        z_score_entry_threshold=2.0,
        z_score_exit_threshold=0.5,
        lookback_window=20,
        cooldown_bars=2,
    )


def _decide(strategy: MeanReversionStrategy, marks, position_state=None):
    return strategy.decide(
        symbol="BTC-USD",
        current_mark=marks[-1],
        position_state=position_state,
        recent_marks=marks,
        equity=Decimal("10000"),
        product=None,
    )


def test_injected_cooldown_state_blocks_entry_and_is_caller_visible() -> None:
    state = CooldownState(remaining=2)
    strategy = MeanReversionStrategy(_config(), cooldown=state)

    decision = _decide(strategy, DIP_MARKS)

    assert decision.action == Action.HOLD
    assert "cooldown" in decision.reason.lower()
    assert state.remaining == 1


def test_exit_writes_cooldown_into_the_caller_owned_state() -> None:
    state = CooldownState()
    strategy = MeanReversionStrategy(_config(), cooldown=state)

    decision = _decide(strategy, FLAT_MARKS, position_state=LONG_POSITION)

    assert decision.action == Action.CLOSE
    assert state.remaining == 2
    # A fresh instance handed the same state keeps honoring it: the strategy
    # carries no hidden cooldown of its own.
    successor = MeanReversionStrategy(_config(), cooldown=state)
    assert _decide(successor, DIP_MARKS).action == Action.HOLD
    assert state.remaining == 1


def test_default_cooldown_state_is_per_instance() -> None:
    first = MeanReversionStrategy(_config())
    assert _decide(first, FLAT_MARKS, position_state=LONG_POSITION).action == Action.CLOSE

    # The CLOSE above armed first's own cooldown; a separately constructed
    # instance starts clear and may enter immediately (today's live behavior).
    second = MeanReversionStrategy(_config())
    assert _decide(second, DIP_MARKS).action == Action.BUY
