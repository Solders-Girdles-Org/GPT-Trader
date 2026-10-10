# Live Trade

Real-time trading infrastructure for spot and perpetuals markets.

## Architecture

The live trading system is organized into focused subsystems:

```
live_trade/
├── engines/          # Coordinator engines (telemetry, execution)
├── risk/             # Risk management (pre-trade, runtime guards)
├── risk_runtime/     # Runtime circuit breakers and position monitoring
├── strategies/       # Trading strategies (baseline, mean_reversion, regime_switcher)
├── guard_errors.py   # Error hierarchy for risk guards
└── primitives.py     # Core types (signals, orders)
```

## Key Components

| Component | Purpose |
|-----------|---------|
| `engines/` | WebSocket coordinators, background tasks |
| `risk/` | Pre-trade validation, leverage limits |
| `risk_runtime/` | Runtime guards, circuit breakers |
| `strategies/` | Strategy implementations |

## Risk Management

Risk is enforced at two levels:

1. **Pre-trade** (`risk/`): Validates orders before submission
2. **Runtime** (`risk_runtime/`): Monitors positions and triggers circuit breakers

## Strategy Interface

Strategies implement the `StrategyProtocol` and receive market data via coordinator engines.

## Related Packages

- `src/gpt_trader/app/` - Application bootstrap and DI container
- `src/gpt_trader/features/brokerages/` - Exchange connectivity
- `src/gpt_trader/monitoring/` - Alerts and telemetry
