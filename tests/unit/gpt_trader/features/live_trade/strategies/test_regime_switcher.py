from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

import pytest

from gpt_trader.features.intelligence.regime import (
    MarketRegimeDetector,
    RegimeState,
    RegimeType,
)
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision
from gpt_trader.features.live_trade.strategies.regime_switcher import (
    RegimeSwitchingStrategy,
)


class _StubDetector:
    def __init__(self, regimes: Sequence[RegimeType]):
        self._regimes = list(regimes)
        self._idx = 0

    def update(self, symbol: str, price: Decimal) -> RegimeState:
        if not self._regimes:
            regime = RegimeType.UNKNOWN
        else:
            regime = self._regimes[min(self._idx, len(self._regimes) - 1)]
        self._idx += 1
        return RegimeState(
            regime=regime,
            confidence=1.0,
            trend_score=0.0,
            volatility_percentile=0.5,
            momentum_score=0.0,
            regime_age_ticks=1,
            transition_probability=0.0,
        )


class _StubStrategy:
    def __init__(self, decisions: Sequence[Decision]):
        self._decisions = list(decisions)
        self.calls: int = 0

    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Any,
        market_data: Any = None,
        candles: Sequence[Any] | None = None,
    ) -> Decision:
        decision = self._decisions[min(self.calls, len(self._decisions) - 1)]
        self.calls += 1
        return decision

    def rehydrate(self, events: Sequence[dict[str, Any]]) -> int:
        return 0


def _pos_long() -> dict[str, Any]:
    return {"quantity": Decimal("1"), "side": "long", "entry_price": Decimal("100")}


def _pos_short() -> dict[str, Any]:
    return {"quantity": Decimal("1"), "side": "short", "entry_price": Decimal("100")}


def _build_regime_follow_strategy(
    regime: RegimeType,
    *,
    enable_shorts: bool = True,
) -> RegimeSwitchingStrategy:
    detector = _StubDetector([regime])
    trend = _StubStrategy([Decision(Action.HOLD, "trend", indicators={"from": "trend"})])
    mean_rev = _StubStrategy([Decision(Action.HOLD, "mr", indicators={"from": "mr"})])
    return RegimeSwitchingStrategy(
        trend_strategy_factory=lambda: trend,
        mean_reversion_strategy_factory=lambda: mean_rev,
        regime_detector=cast(MarketRegimeDetector, detector),
        trend_mode="regime_follow",
        enable_shorts=enable_shorts,
    )


@pytest.mark.parametrize(
    ("regime", "position_state", "enable_shorts", "expected_action", "reason_fragment"),
    [
        (RegimeType.BULL_QUIET, None, True, Action.BUY, "bull"),
        (RegimeType.BEAR_QUIET, _pos_long(), True, Action.CLOSE, "bear"),
        (RegimeType.BULL_VOLATILE, _pos_short(), True, Action.CLOSE, "bull"),
        (RegimeType.BEAR_QUIET, None, True, Action.SELL, "bear"),
        (RegimeType.BEAR_VOLATILE, None, False, Action.HOLD, "shorts disabled"),
    ],
)
def test_regime_follow_actions(
    regime: RegimeType,
    position_state: dict[str, Any] | None,
    enable_shorts: bool,
    expected_action: Action,
    reason_fragment: str,
) -> None:
    strategy = _build_regime_follow_strategy(regime, enable_shorts=enable_shorts)

    decision = strategy.decide(
        symbol="BTC-USD",
        current_mark=Decimal("100"),
        position_state=position_state,
        recent_marks=[Decimal("100")] * 70,
        equity=Decimal("10000"),
        product=None,
    )

    assert decision.action == expected_action
    assert reason_fragment in decision.reason


class TestRegimeSwitchingStrategy:
    def test_delegates_by_regime_when_flat(self) -> None:
        detector = _StubDetector([RegimeType.SIDEWAYS_QUIET])
        trend = _StubStrategy([Decision(Action.SELL, "trend", indicators={"from": "trend"})])
        mean_rev = _StubStrategy([Decision(Action.BUY, "mr", indicators={"from": "mr"})])

        strategy = RegimeSwitchingStrategy(
            trend_strategy_factory=lambda: trend,
            mean_reversion_strategy_factory=lambda: mean_rev,
            regime_detector=cast(MarketRegimeDetector, detector),
        )

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=None,
            recent_marks=[Decimal("100")] * 70,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.BUY
        assert decision.indicators["from"] == "mr"
        assert decision.indicators["regime"] == "SIDEWAYS_QUIET"
        assert decision.indicators["regime_switcher_selected"] == "mean_reversion"

    def test_sticky_strategy_controls_position_until_close(self) -> None:
        detector = _StubDetector(
            [RegimeType.SIDEWAYS_QUIET, RegimeType.BULL_QUIET, RegimeType.BULL_QUIET]
        )
        trend = _StubStrategy([Decision(Action.CLOSE, "trend_close", indicators={"from": "trend"})])
        mean_rev = _StubStrategy(
            [
                Decision(Action.BUY, "mr_entry", indicators={"from": "mr"}),
                Decision(Action.HOLD, "mr_hold", indicators={"from": "mr"}),
                Decision(Action.CLOSE, "mr_close", indicators={"from": "mr"}),
            ]
        )

        strategy = RegimeSwitchingStrategy(
            trend_strategy_factory=lambda: trend,
            mean_reversion_strategy_factory=lambda: mean_rev,
            regime_detector=cast(MarketRegimeDetector, detector),
        )

        first = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=None,
            recent_marks=[Decimal("100")] * 70,
            equity=Decimal("10000"),
            product=None,
        )
        assert first.action == Action.BUY
        assert first.indicators["regime_switcher_active"] == "mean_reversion"

        second = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("101"),
            position_state=_pos_long(),
            recent_marks=[Decimal("100")] * 70,
            equity=Decimal("10000"),
            product=None,
        )
        assert second.action == Action.HOLD
        assert second.indicators["from"] == "mr"
        assert second.indicators["regime"] == "BULL_QUIET"
        assert second.indicators["regime_switcher_selected"] == "mean_reversion"
        assert second.indicators["regime_switcher_sticky_used"] is True

        third = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("102"),
            position_state=_pos_long(),
            recent_marks=[Decimal("100")] * 70,
            equity=Decimal("10000"),
            product=None,
        )
        assert third.action == Action.CLOSE
        assert third.indicators["regime_switcher_active"] is None

    def test_crisis_closes_position(self) -> None:
        detector = _StubDetector([RegimeType.CRISIS])
        trend = _StubStrategy([Decision(Action.HOLD, "trend", indicators={"from": "trend"})])
        mean_rev = _StubStrategy([Decision(Action.HOLD, "mr", indicators={"from": "mr"})])

        strategy = RegimeSwitchingStrategy(
            trend_strategy_factory=lambda: trend,
            mean_reversion_strategy_factory=lambda: mean_rev,
            regime_detector=cast(MarketRegimeDetector, detector),
        )

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=_pos_long(),
            recent_marks=[Decimal("100")] * 70,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.CLOSE
        assert decision.indicators["regime"] == "CRISIS"
