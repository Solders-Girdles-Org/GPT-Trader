# Backtesting

Historical strategy simulation and performance analysis.

## Purpose

This package provides a backtesting framework for validating trading strategies:

- **Simulation**: Order matching and position tracking
- **Slippage modeling**: Configurable slippage and fee models
- **Performance analysis**: Equity curves, drawdown, trade statistics

## Key Components

| Module | Purpose |
|--------|---------|
| `simulation/broker.py` | SimulatedBroker for order execution |
| `simulation/matching.py` | Order matching engine |
| `results/` | Backtest result analysis |
| `reporting/` | Performance reports and charts |

## Usage

```python
from gpt_trader.backtesting.simulation import SimulatedBroker

broker = SimulatedBroker(initial_balance=10000)

# Run strategy with historical data
for candle in historical_candles:
    signal = strategy.evaluate(candle)
    if signal:
        broker.place_order(...)

# Analyze results
equity_curve = broker.get_equity_curve()
```

## Simulation Features

- Market and limit order support
- Position sizing and leverage
- Fee modeling (maker/taker)
- Slippage estimation

## Related Packages

- `features/data/` - Historical data loading
