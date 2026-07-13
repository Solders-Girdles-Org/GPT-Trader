# Paper Trading Guide

---
status: current
consolidates:
  - PAPER_TRADING_IMPLEMENTATION.md
  - PAPER_ENGINE_DECOUPLING.md
  - PAPER_TRADING_PROGRESS.md
  - PAPER_TRADING_SESSION_REPORT.md
---

## Overview

Paper trading provides risk-free simulation of trading strategies using simulated execution.
The `paper` and `dev` profiles run with `mock_broker` enabled, so no real orders
or API calls are made.

## Implementation

### Deterministic Broker
The default paper workflow uses the deterministic broker stub:
- Deterministic fills for testing
- Synthetic quotes (no external market data calls)
- Immediate execution with predictable order IDs

Implementation: `src/gpt_trader/features/brokerages/mock/deterministic.py`.

### Hybrid Paper Broker (experimental)
`src/gpt_trader/features/brokerages/paper/hybrid.py` supports real market data
with simulated execution. It is **not wired** in the default broker factory.
Use it only for experiments (custom container/broker factory).

### Configuration
```bash
# Paper profile (mock broker + dry run)
uv run gpt-trader run --profile paper

# Single-cycle smoke test
uv run gpt-trader run --profile paper --dev-fast
```

### Programmatic Entry (Python)

```python
from gpt_trader.app.container import ApplicationContainer
from gpt_trader.cli.services import load_profile_config
from gpt_trader.config.types import Profile

config = load_profile_config(Profile.PAPER)
bot = ApplicationContainer(config).create_bot()
```

### Module Layout

    config/profiles/paper.yaml        # Paper profile settings
    src/gpt_trader/features/brokerages/mock/deterministic.py
    src/gpt_trader/features/brokerages/paper/hybrid.py  # Experimental
    src/gpt_trader/features/live_trade/strategies/      # Strategy implementations

### Strategy Catalog

Paper mode uses the same strategies as live trading:
1. `baseline` – MA + RSI baseline
2. `mean_reversion` – Z-score mean reversion
3. `ensemble` – signal ensemble architecture

## Features

### Market Simulation
- Synthetic quotes from the deterministic broker
- Immediate fills with predictable IDs
- No external API calls

### Risk-Free Testing
- Test strategies without capital
- Validate order logic
- Debug execution paths
- Performance benchmarking

## Usage

### Quick Start
```bash
# Run with deterministic broker
uv run gpt-trader run --profile paper --dev-fast

# Monitor performance
tail -f ${COINBASE_TRADER_LOG_DIR:-var/logs}/coinbase_trader.log | grep "PnL"
```

### Advanced Configuration
```python
# Custom paper trading settings
from decimal import Decimal
from gpt_trader.features.brokerages.mock import DeterministicBroker

broker = DeterministicBroker(equity=Decimal("100000"))
broker.set_mark("BTC-PERP", Decimal("50000"))
# Set container._broker = broker before calling container.create_bot()
```

## Stage 1 Paper Loop — One Honest Day

The Stage 1 loop from [Direction](DIRECTION.md) — propose, review, paper-execute,
attribute, report — can be run today as an operator procedure on real market
data with no credentials and no broker access. Running it end to end is the
project's ground truth: a component is trusted when its output shows up in the
artifacts this loop produces (snapshots, audit events, closeouts, the report).

The automated offline equivalent runs in CI and locally via `make stage1-smoke`
(`scripts/ops/stage1_rails_smoke.py`), so the loop cannot silently break. The
manual day below adds the real-data half.

```bash
# 0. Inspect the risk budget; attest account equity if unset (human action)
uv run gpt-trader ideas budget show
uv run gpt-trader ideas budget set --account-equity <equity> \
  --actor <you> --reason "attest equity for paper day"

# 1. Record the market view (read-only public candles -> snapshot artifact)
uv run gpt-trader ideas snapshot build --from-coinbase \
  --symbols BTC-USD,ETH-USD --granularity ONE_HOUR --lookback 200 \
  --out var/data/snapshots/paper-day.json

# 2. Propose from the recorded snapshot (deterministic proposer, ai actor)
uv run gpt-trader ideas propose-baseline --snapshot var/data/snapshots/paper-day.json

# 2-alt. Or run a live-trade strategy as the proposer over the same snapshot
#        (the strategy->proposer parity lane; same audited service, ai actor)
uv run gpt-trader ideas propose-strategy --snapshot var/data/snapshots/paper-day.json \
  --strategy baseline-spot

# 3. Review the queue and decide (human actions)
uv run gpt-trader ideas queue-status
uv run gpt-trader ideas list --state proposed
uv run gpt-trader ideas show <decision-id>
uv run gpt-trader ideas approve <decision-id> --actor <you> --reason "<why>"
# ...or: ideas reject / ideas request-changes

# 4. Paper-execute the approved idea (machine loop, no attestation).
#    Places one simulated market order on the offline deterministic paper
#    broker (client_order_id = decision id) and records the submission and
#    fill on the audit log; live brokers are structurally unreachable here.
#    Pass the price you observe so the simulated fill is honest.
uv run gpt-trader ideas execute-paper <decision-id> --mark <observed-price>

# 4-alt. Or paper-execute by hand against a real external venue: export the
#        ticket and attest the lifecycle yourself (no broker API calls).
uv run gpt-trader ideas export-ticket --decision-id <decision-id> --venue manual
uv run gpt-trader ideas mark-submitted <decision-id> --venue manual \
  --external-order-id <paper-id> --actor <you> --actor-type human
uv run gpt-trader ideas mark-filled <decision-id> --venue manual \
  --external-order-id <paper-id> --actor <you>

# 5. Attribute the outcome and close the day
uv run gpt-trader ideas closeout record <decision-id> --resolution thesis_target \
  --realized-profit-loss-amount <amount> --actor <you>
uv run gpt-trader ideas report
uv run gpt-trader ideas audit verify
```

Every step stamps an actor into the append-only audit log; proposals come from
`ai` actors, human review approvals from `human` actors, and `execute-paper`
lifecycle events from the `paper-idea-executor` system actor under the `paper`
venue. System approvals from `ideas approve --auto-sweep` reach paper execution
only when the operator separately enables `GPT_TRADER_IDEAS_AUTO_EXECUTION` and
the audited autonomy log resolves to `bounded_autonomy`. None of these commands
touch a live broker or account. Ideas that expire unreviewed are swept with
`uv run gpt-trader ideas expire`.

## Scheduled Stage 1 Turns (Unattended Operation)

`ideas cycle` runs exactly one turn of the paper loop — lock, snapshot, expire
sweep, proposers, paper-execute already-APPROVED ideas priced from the turn's
own snapshot, report/queue artifacts, one manifest row. Recurrence comes from
an external scheduler (launchd or cron); the command never decides a cadence,
never approves ideas, and never contacts a live broker or account. Approvals
remain a separate event: review the queue between turns exactly as in the
honest-day procedure above, or use the default-off auto-approval and
auto-execution gates documented in
[stage2-auto-approval-workflow](decisions/stage2-auto-approval-workflow.md) and
[stage2-execution-gate](decisions/stage2-execution-gate.md).

Prerequisite: attest account equity once before scheduling turns
(`ideas budget set --account-equity <equity> --actor <you> --reason "..."`,
step 0 of the honest day above). Proposal sizing and the approval gate both
denominate against the attested equity; on an unattested root the cycle still
runs, but every sized proposal fails approval with `account_equity_snapshot
is required to verify max_open_notional_pct budget exposure` until a human
attests equity.

The default conservative configuration lives in
`scripts/ops/stage1_cycle_turn.sh`: eight liquid Coinbase USD spot pairs
(BTC, ETH, SOL, XRP, LTC, LINK, AVAX, DOT — all quoted above $1 so the default
price precision of 0.01 stays meaningful), ONE_HOUR candles, lookback 200,
default proposers `baseline` and `regime-aware`. A universe of just two symbols
starves track-record depth: the busy-instrument skip admits at most one open
idea per instrument, so per-turn proposal flow scales with the instrument set
(issue #1215). Symbols, granularity, lookback, price precision, and the
proposer set are env-overridable there (`CYCLE_SYMBOLS`, `CYCLE_GRANULARITY`,
`CYCLE_LOOKBACK`, `CYCLE_PRICE_PRECISION`, and space-separated
`CYCLE_PROPOSERS`, for example `CYCLE_PROPOSERS=baseline`); cadence belongs
only in the scheduler entry.

Shrinking `CYCLE_SYMBOLS` never strands an unresolved trade: each turn tops the
snapshot fetch up with any instrument that still has an open idea or a filled
idea awaiting closeout, so the exit monitor keeps receiving candles until the
position resolves. Top-up fetches are per-symbol and non-fatal — a delisted
instrument defers its own resolution without failing the turn.

Sessioned instruments also fail safely: the paper executor leaves an approved
equity idea unsubmitted while XNYS is closed, and the exit monitor leaves a
filled equity idea unresolved rather than marking it against closed-session
data. Both outcomes are explicit in the cycle manifest and retry on a later
turn. Crypto remains 24x7; an instrument that cannot be classified to a known
session is refused rather than silently assigned crypto semantics.

Strategy-backed proposers — the live strategy library running over the turn's
snapshot through the `Proposer` contract — are opt-in until replay parity is
demonstrated: pass `--proposer strategy-baseline-spot` (or
`strategy-baseline-perps`, `strategy-mean-reversion`,
`strategy-regime-switcher`; all emit long-only spot ideas), or set
`CYCLE_PROPOSERS="baseline regime-aware strategy-baseline-spot"`. Each
strategy proposes only past its own live warm-up floor: the baseline family
and mean reversion need 20 candles, while the regime switcher holds until its
detector confirms a regime at 54 candles — keep `--lookback` comfortably
above that (the default 200 is). For sub-cent symbols the proposal price
levels quantize to zero at the default precision and fail closed; pass a
finer `--price-precision` for the turn.

### Bounded-autonomy turns (Stage 2)

`scripts/ops/stage2_cycle_turn.sh` runs the same turn under bounded autonomy. It
sets the two audited Stage-2 gates (`GPT_TRADER_IDEAS_AUTO_APPROVAL=1`,
`GPT_TRADER_IDEAS_AUTO_EXECUTION=1`), system-approves every violation-free
proposal inside the budget envelope (`ideas approve --auto-sweep`), then runs one
cycle turn so those approvals paper-execute against the turn's snapshot. It is
paper-only and never contacts a live broker. To run unattended Stage-2 turns,
point the launchd/cron entry below at `stage2_cycle_turn.sh` instead of
`stage1_cycle_turn.sh`; the same env overrides (`CYCLE_SYMBOLS`, etc.), overlap
lock, and manifest-row evidence contract apply.

Enabling Stage 2 is an operator act with two audited preconditions:

- The autonomy log resolves to `bounded_autonomy` (`ideas autonomy show`). Set it
  with `ideas autonomy set bounded_autonomy --actor <you> --reason "..."`. Both
  gates silently no-op under any other mode, so a breach that ratchets autonomy
  down (daily-loss or drawdown-from-peak) halts auto-approval and auto-execution
  until the mode is re-earned — the down-ratchet is audited, never silent.
- Account equity is attested (the same Stage-1 prerequisite above).

Reverting is immediate and reversible: point the scheduler back at
`stage1_cycle_turn.sh`, or run
`ideas autonomy set human_approved_execution --actor <you> --reason "..."` to drop
the audited mode so both gates no-op regardless of the env vars.

### launchd (macOS)

Save as `~/Library/LaunchAgents/com.gpt-trader.stage1-cycle.plist`, replacing
the repository path, then `launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.gpt-trader.stage1-cycle</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/ABSOLUTE/PATH/TO/GPT-Trader/scripts/ops/stage1_cycle_turn.sh</string>
  </array>
  <!-- Cadence lives here, not in code: hourly at :05 -->
  <key>StartCalendarInterval</key>
  <array><dict><key>Minute</key><integer>5</integer></dict></array>
  <key>StandardOutPath</key>
  <string>/tmp/gpt-trader-stage1-cycle.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/gpt-trader-stage1-cycle.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.gpt-trader.stage1-cycle.plist
launchctl kickstart gui/$(id -u)/com.gpt-trader.stage1-cycle   # run one turn now
```

### cron

```cron
5 * * * * /ABSOLUTE/PATH/TO/GPT-Trader/scripts/ops/stage1_cycle_turn.sh >> /tmp/gpt-trader-stage1-cycle.log 2>&1
```

Both schedulers start jobs with a minimal `PATH`; the wrapper prepends
`~/.local/bin` (Astral `uv` installer), `/opt/homebrew/bin`, and
`/usr/local/bin` itself, so neither entry needs environment configuration.

### Overlap and failure semantics

- The turn takes a lock on `<ideas-root>/cycle`. An overlapping invocation
  fails fast with a validation error ("Another paper-cycle turn is already
  running") and appends **no** manifest row — the running turn's row is the
  evidence for that slot, so a schedule that occasionally overlaps is safe.
- A failed turn (network down, bad snapshot) appends an honest
  `"outcome": "failed"` row with the error and exits nonzero. Failed turns are
  evidence too; do not delete them.

### Evidence: consecutive unattended days

Every turn appends exactly one JSON line to
`<ideas-root>/cycle/manifest.jsonl` (default ideas root
`var/data/trade_ideas`). A day counts toward the streak when it has at least
one manifest row and every row that day completed:

```bash
python3 - <<'EOF'
import datetime, json, pathlib

manifest = pathlib.Path("var/data/trade_ideas/cycle/manifest.jsonl")
days: dict[datetime.date, bool] = {}
for line in manifest.read_text().splitlines():
    row = json.loads(line)
    day = datetime.date.fromisoformat(row["started_at"][:10])
    days[day] = days.get(day, True) and row["outcome"] == "completed"

streak, day = 0, max(days, default=None)
while day in days and days[day]:
    streak += 1
    day -= datetime.timedelta(days=1)
print(f"consecutive clean days: {streak} (through {max(days, default='n/a')})")
EOF
```

## Replay Evidence for the Stage 1 → 2 Scorecard

Three scorecard gates (`risk_calibration`, `expectancy`, `benchmark_edge`)
need closed-idea outcomes, and the wall-clock observation window is 60 days.
Replay evidence makes those quantities readable now, without waiting:
`ideas replay tournament` replays proposers point-in-time over a recorded
snapshot window, and `ideas scorecard --replay-report <path>` reports the
result as replay-labeled evidence **alongside** the wall-clock gates — it is
never blended into a gate verdict, so the two evidence classes stay
distinguishable in every output.

One replay-evidence turn is scripted:

```bash
scripts/ops/replay_evidence.sh
```

The script mirrors the Stage-2 cycle turn's universe and granularity so the
replay measures the proposer set that is actually running: it snapshot-builds
the paper universe from read-only public Coinbase candles
(`ideas snapshot build --from-coinbase`), runs one tournament per symbol with
the active pair plus the strategy-backed convergence proposers
(`baseline-ma-10-50,regime-aware-ma-10-50,strategy-mean-reversion,strategy-regime-switcher`
— the deterministic baseline is the benchmark side of the edge comparison,
and the strategy-backed ids add genuine decision diversity, #1245), then
renders the scorecard with all reports attached. The default lookback is 720
hourly candles (~30 days) so the window samples more regime variety than a
single 300-candle slice; expect a few minutes per symbol. Each run writes a self-contained
directory under `var/data/trade_ideas/replay_evidence/<UTC timestamp>/`:
the snapshot, per-symbol tournament reports, and the durable scorecard
artifact. Override `REPLAY_SYMBOLS`, `REPLAY_GRANULARITY`, `REPLAY_LOOKBACK`,
`REPLAY_PROPOSERS`, `REPLAY_PRICE_PRECISION`, or `REPLAY_EVIDENCE_DIR` per
invocation (the replay price grid defaults to 0.0001, finer than the cycle's
0.01, so low-priced symbols keep distinct stop/entry/target levels instead
of failing the strategy adapter's fail-closed precision guard); everything is
broker-free and never reads accounts or places orders. A symbol whose
tournament fails keeps its error envelope in the run directory as evidence
but is excluded from the scorecard render; the turn only fails when every
tournament fails.

To attach replay evidence to any scorecard render by hand:

```bash
uv run gpt-trader ideas scorecard \
  --replay-report var/data/trade_ideas/replay_evidence/<run>/tournament_BTC-USD.json \
  --replay-report var/data/trade_ideas/replay_evidence/<run>/tournament_ETH-USD.json
```

Both raw replay artifacts and the CliResponse envelope written by
`--format json --output <path>` are accepted. Interpretation: per-proposer
`target_hit_rate` / `stop_hit_rate` are the replay read on risk calibration,
`average_return_r` is the replay read on expectancy, and the `edge vs
baseline-ma-…` lines are the replay read on benchmark edge. When the replay
artifact carries sizing (`capital_weighted_avg_r=… (n=…)`), that row is the
only aggregate that can see the sizing channel — proposers with identical
levels but different notional commitment separate there and nowhere else.
Overlay proposers additionally print a `counterfactuals:` line (candidates
vs emitted, UNKNOWN skips, suppressions by regime, exit plans adjusted, and
the emitted-ideas regime distribution) so a decision channel that never
fires is visible in every run instead of requiring a manual audit — the M5
diagnosis found regime suppression had fired 0/84 times with nothing
reporting it.

## Readiness Evidence Inputs

Paper trading produces evidence that feeds the readiness checklist; it does not
itself authorize live execution. Live profiles only run after the gates in
[Live Operations](production.md) and the
[Direction](DIRECTION.md) have been
satisfied with explicit human approval.

### What paper runs should produce
1. Multi-day paper sessions with daily reports archived
2. Strategy/risk metrics measured against the readiness pillars
3. Reviewed error and guard logs

### Dry-run validation of profile wiring
```bash
# Validate canary profile settings without exchange orders
uv run gpt-trader run --profile canary --dry-run
```

For any live profile run, follow the gate sequence in
[Live Operations](production.md#live-gate-sequence). Do not promote past
`--dry-run` without recorded approval.

## Performance Metrics

Track these metrics during paper trading:
- Win rate (target > 55%)
- Sharpe ratio (target > 1.0)
- Maximum drawdown (limit < 10%)
- Average trade duration
- Risk/reward ratio

## Best Practices

1. **Extended Testing**: Run paper trading for at least 100 trades
2. **Market Conditions**: Test across different market regimes
3. **Stress Testing**: Simulate extreme market conditions
4. **Logging**: Keep detailed logs for analysis
5. **Gradual Scaling**: Start with tiny positions when going live
