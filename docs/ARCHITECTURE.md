# System Architecture

---
status: current
---

GPT-Trader is a Coinbase-oriented trading system with two lanes over one core.
The **recorded experiment** is the owner-facing product
([decision](decisions/recorded-experiment-product.md)): recorded bars in,
explained decisions, bounded simulated fills and reconciled cash out, with no
broker, scheduler or credentials. The **retained runtime** is the paper/live
spine on the staged-autonomy ladder in [DIRECTION.md](DIRECTION.md): market
data through proposers, an audited approval workflow, a risk kernel and guard
stack, into a paper broker today and, only after the gates there, a live one.
This document describes structure. [STATUS.md](STATUS.md) points at what is
shipped; [decisions/](decisions/README.md) hold the rationale.

## Packages

All code lives under `src/gpt_trader/`; tests mirror these paths under
`tests/unit/`.

| Package | Role |
| --- | --- |
| `core/`, `errors/`, `validation/`, `config/`, `utilities/`, `logging/` | Domain types, order intents, fill accounting, error taxonomy; no upward imports |
| `features/brokerages/` | Adapters: `coinbase/` (REST and WebSocket, CDP JWT auth, spot and CFM futures), `paper/`, `mock/`, `robinhood/` (authenticated reads and non-binding previews only), and the broker factory |
| `features/recorder/` | Read-only observation: ticker polling into the price-tick store; candle history into point-in-time `MarketSnapshot` artifacts |
| `features/trade_ideas/` | The spine: `TradeIdea` records, `TradeIdeaService` (the one identity-stamped, audited path for every actor), eligibility, versioned `RiskBudget`, audited autonomy state, `RiskKernel`, portfolio monitors, accounting, scorecard, transactional persistence. Imports only `core` and `errors` |
| `features/idea_execution/` | Paper lane: `PaperIdeaExecutor` (live brokers structurally unreachable), the batch cycle turn, the in-process event lane, the exit monitor |
| `features/live_trade/` | Retained bot: `TradingBot`, `TradingEngine`, strategies (`baseline`, `mean_reversion`, plus the TA families measured dead in [adopt-agentic-alpha-direction](decisions/adopt-agentic-alpha-direction.md)), `LiveRiskManager`, `GuardManager` and its guards, `DegradationState`, `OrderSubmitter`, `BrokerExecutor` |
| `features/experiment/` | The recorded experiment: input binding, transition engine, atomic journal |
| `features/strategy_tools/`, `features/intelligence/`, `features/data/`, `features/optimize/`, `features/strategy_dev/`, `backtesting/` | Strategy-to-idea adapter, regime features, data acquisition, parameter search, benchmark replay |
| `app/` | `ApplicationContainer` composition root, `BotConfig` and `ProfileLoader`, runtime paths, risk-budget seeding |
| `persistence/` | SQLite event and order stores with JSONL fallback |
| `monitoring/`, `preflight/`, `security/` | Health checks, metrics, alerts, daily report; readiness preflight; secrets and input validation |
| `cli/`, `web/` | The `gpt-trader` commands (`experiment`, `run`, `ideas`, `record`, `console`, `report`, `preflight` and others) and the FastAPI operator console; both are thin adapters over the services |

## Data flow

**Recorded experiment.** `gpt-trader experiment run --input <json> --root <dir>`
binds the input (recorded hourly bars and settings) into the journal, then per
bar: a signal from closed history only, admission at the next bar's open with
size recomputed from current cash and costs, conservative OHLC fills, and one
atomic journal entry holding data, decision, fills, cash, fees, controls and
the resume checkpoint. History is append-only; a resume advances only missing
observations; a changed source or input is a new experiment. Cash and results
are reconciled independently through `core/fill_accounting.py`; reports derive
from a consistent snapshot and own no state.

**Paper and live spine.**

1. **Observe.** The recorder polls read-only tickers and candles; the snapshot
   builder turns them into a `MarketSnapshot`.
2. **Propose.** A `Proposer` (deterministic benchmark today; the reasoning
   analyst is scoped in #1252) returns complete `TradeIdea` records with entry,
   invalidation, exit, max loss and expiry. Live strategies reach the same
   record through the default-off `features/strategy_tools/trade_idea_adapter.py`.
3. **Admit.** `TradeIdeaService.propose` appends to the audit log. Every
   approval and every execution consults `RiskKernel` once: eligibility
   invariants, the current `RiskBudget` version, and the audited autonomy
   level (`human_approved_execution` by default, ratcheting down on breach).
   Humans decide through the `ideas` commands or the console; Stage 2
   auto-approval applies only inside the budget envelope.
4. **Execute on paper.** `PaperIdeaExecutor` places one simulated market order
   per approved idea (`client_order_id` = decision id) and records
   SUBMITTED and FILLED through the service. The cycle turn runs steps 1-4
   once under an external scheduler and leaves a manifest; the event lane runs
   them in-process per strategy event.
5. **Execute live (gated).** In the retained bot,
   `TradingEngine._validate_and_place_order` runs pre-trade validation,
   `LiveRiskManager` limits, `GuardManager` (API health, daily loss,
   liquidation buffer, mark staleness, PnL telemetry, risk metrics,
   volatility) and `DegradationState` pauses (global or per symbol, optionally
   reduce-only); then `OrderSubmitter` persists the intent and `BrokerExecutor`
   talks to the broker under retry and timeout policy. A missing
   acknowledgment stays uncertain until reconciled. No live order is submitted
   without the recorded approval in [DIRECTION.md](DIRECTION.md); the `canary`
   and `prod` profiles are assets, not approval.
6. **Account and measure.** Monitors compute high-water mark, drawdown from
   peak and open exposure from the same ledger; closeout attribution and the
   scorecard grade the track record that promotion requires.

`TradingBot.flatten_and_stop()` bypasses the guard stack on purpose so that
emergency closure succeeds during a risk trip.

## Composition and boundaries

`ApplicationContainer` wires the retained runtime; the experiment library takes
explicit dependencies and never touches the container
([DI_POLICY.md](DI_POLICY.md)). Profiles (`config/profiles/*.yaml` through
`ProfileLoader` into `BotConfig`) fail closed when invalid and do not
configure experiments. `scripts/ci/check_import_boundaries.py` enforces the
edges: no slice imports the CLI, preflight or the container; `monitoring` does
not import `features` at runtime; `trade_ideas` imports only `core` and
`errors`; cross-slice edges are an allowlist that only shrinks; the web
console reaches only its trade-idea adapter contract. Layer detail:
[BOUNDARIES](architecture/BOUNDARIES.md), [SEAMS](architecture/SEAMS.md),
[ENTRYPOINTS](architecture/ENTRYPOINTS.md),
[OWNERSHIP](architecture/OWNERSHIP.md).

## Where evidence and results live

| Evidence | Location |
| --- | --- |
| Experiment journal and reports | The explicit experiment root, normally `runtime_data/experiments/<name>/` |
| Trade-idea records and the append-only audit, risk-budget, autonomy, closeout and paper-execution streams | The ideas root (`var/data/trade_ideas` by default, `GPT_TRADER_IDEAS_ROOT` to override) under one SQLite transaction boundary ([contract](decisions/transactional-trade-state.md)); legacy JSONL files are import/export only |
| Paper cycle turns | `manifest.jsonl` and `runs/<run_id>/` under the cycle root |
| Runtime events, orders, readiness reports | `runtime_data/<profile>/` |
| Readiness and live gates | [READINESS.md](READINESS.md), [production.md](production.md), `preflight/` |
| Guard behaviour and degradation | [RELIABILITY.md](RELIABILITY.md) |
| Operating procedures | [paper_trading.md](paper_trading.md) |

Nothing under `runtime_data/` or `var/` is committed. Source integration never
proves that a store was migrated, a lane was authorized or a gate was passed.
