# Trading Strategies

Strategy implementations for live and backtested trading.

## Architecture

Strategies follow a common interface and receive market data from the trading engine:

```
strategies/
├── baseline/           # Baseline MA/RSI strategy (spot-first)
├── mean_reversion/     # Mean reversion strategy
├── regime_switcher/    # Regime switching strategy
├── ensemble.py         # Signal ensemble strategy
└── base.py             # StrategyProtocol + MarketDataContext
```

## Strategy Protocol

All strategies implement `StrategyProtocol` (see `base.py`):

```python
class StrategyProtocol(Protocol):
    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Product | None,
        market_data: MarketDataContext | None = None,
    ) -> Decision:
        """Generate a trading decision."""
        ...
```

## Built-in Strategies

| Strategy | Market | Description |
|----------|--------|-------------|
| `perps_baseline` (module: `baseline/`) | Spot/CFM | Moving average crossover baseline |
| `mean_reversion` | Spot/CFM | Z-score mean reversion |
| `regime_switcher` | Spot/CFM | Regime-based strategy selection |

## Creating Custom Strategies

1. Create a new package under `strategies/`
2. Implement `StrategyProtocol`
3. Register in the strategy factory
4. Configure via profile YAML

## Related Packages

- `features/live_trade/risk/` - Pre-trade validation
- `features/live_trade/engines/strategy.py` - Strategy evaluation and order routing
