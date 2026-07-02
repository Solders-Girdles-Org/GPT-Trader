from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from gpt_trader.features.live_trade.signals.protocol import StrategyContext
from gpt_trader.features.live_trade.signals.types import SignalOutput, SignalType
from gpt_trader.features.live_trade.strategies.baseline import Action
from gpt_trader.features.live_trade.strategies.ensemble import (
    EnsembleStrategy,
    EnsembleStrategyConfig,
)


@dataclass
class StubSignal:
    output: SignalOutput | None = None
    should_raise: bool = False

    def generate(self, context: StrategyContext) -> SignalOutput:
        if self.should_raise:
            raise RuntimeError("boom")
        if self.output is None:
            raise RuntimeError("missing output")
        return self.output


@dataclass
class StubCombiner:
    output: SignalOutput
    received_signals: list[SignalOutput] | None = None

    def combine(self, signals: list[SignalOutput], context: StrategyContext) -> SignalOutput:
        self.received_signals = list(signals)
        return self.output


def _make_strategy(
    *,
    strength: float,
    confidence: float = 0.5,
    metadata: dict | None = None,
    config: EnsembleStrategyConfig | None = None,
) -> tuple[EnsembleStrategy, StubCombiner]:
    output = SignalOutput(
        name="combined",
        type=SignalType.OTHER,
        strength=strength,
        confidence=confidence,
        metadata=metadata or {"source": "stub"},
    )
    combiner = StubCombiner(output=output)
    strategy = EnsembleStrategy(config=config, signals=[], combiner=combiner)
    return strategy, combiner


def _base_context_kwargs() -> dict:
    return {
        "symbol": "BTC-USD",
        "current_mark": Decimal("100"),
        "position_state": None,
        "recent_marks": [],
        "equity": Decimal("1000"),
        "product": None,
        "market_data": None,
    }


def test_entry_thresholds_buy_sell_and_hold() -> None:
    config = EnsembleStrategyConfig(buy_threshold=0.2, sell_threshold=-0.2)

    strategy, _combiner = _make_strategy(strength=0.3, config=config)
    decision = strategy.decide(**_base_context_kwargs())
    assert decision.action == Action.BUY

    strategy, _combiner = _make_strategy(strength=-0.3, config=config)
    decision = strategy.decide(**_base_context_kwargs())
    assert decision.action == Action.SELL

    strategy, _combiner = _make_strategy(strength=0.2, config=config)
    decision = strategy.decide(**_base_context_kwargs())
    assert decision.action == Action.HOLD

    strategy, _combiner = _make_strategy(strength=-0.2, config=config)
    decision = strategy.decide(**_base_context_kwargs())
    assert decision.action == Action.HOLD


def test_exit_logic_reversals() -> None:
    config = EnsembleStrategyConfig(close_threshold=0.1)
    position_state = {"quantity": Decimal("1"), "side": "long", "entry_price": Decimal("100")}
    context = _base_context_kwargs() | {"position_state": position_state}

    strategy, _combiner = _make_strategy(strength=-0.2, config=config)
    decision = strategy.decide(**context)
    assert decision.action == Action.CLOSE
    assert "Signal reversed to bearish" in decision.reason

    short_state = {"quantity": Decimal("1"), "side": "short", "entry_price": Decimal("100")}
    context = _base_context_kwargs() | {"position_state": short_state}
    strategy, _combiner = _make_strategy(strength=0.2, config=config)
    decision = strategy.decide(**context)
    assert decision.action == Action.CLOSE
    assert "Signal reversed to bullish" in decision.reason


def test_risk_overlay_stop_loss_and_take_profit() -> None:
    config = EnsembleStrategyConfig(stop_loss_pct=0.02, take_profit_pct=0.05)

    long_state = {"quantity": Decimal("1"), "side": "long", "entry_price": Decimal("100")}
    context = _base_context_kwargs() | {
        "position_state": long_state,
        "current_mark": Decimal("97"),
    }
    strategy, _combiner = _make_strategy(strength=0.0, config=config)
    decision = strategy.decide(**context)
    assert decision.action == Action.CLOSE
    assert decision.reason.startswith("Stop Loss")

    context = _base_context_kwargs() | {
        "position_state": long_state,
        "current_mark": Decimal("106"),
    }
    strategy, _combiner = _make_strategy(strength=0.0, config=config)
    decision = strategy.decide(**context)
    assert decision.action == Action.CLOSE
    assert decision.reason.startswith("Take Profit")


def test_signal_generation_failure_is_skipped() -> None:
    ok_output = SignalOutput(
        name="ok",
        type=SignalType.OTHER,
        strength=0.1,
        confidence=0.2,
        metadata={"ok": True},
    )
    signal_ok = StubSignal(output=ok_output)
    signal_bad = StubSignal(output=None, should_raise=True)
    combiner = StubCombiner(
        output=SignalOutput(
            name="combined",
            type=SignalType.OTHER,
            strength=0.0,
            confidence=0.0,
            metadata={},
        )
    )
    strategy = EnsembleStrategy(signals=[signal_bad, signal_ok], combiner=combiner)

    strategy.decide(**_base_context_kwargs())

    assert combiner.received_signals == [ok_output]


def test_decision_metadata_passthrough() -> None:
    metadata = {"alpha": 1}
    strategy, _combiner = _make_strategy(strength=0.0, confidence=0.7, metadata=metadata)

    decision = strategy.decide(**_base_context_kwargs())

    assert decision.confidence == 0.7
    assert decision.indicators == metadata
