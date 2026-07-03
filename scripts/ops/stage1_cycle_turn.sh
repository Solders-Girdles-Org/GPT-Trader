#!/usr/bin/env bash
# One conservative Stage-1 paper cycle turn, for launchd/cron entries.
#
# This wrapper is the default cycle configuration: symbols, granularity,
# lookback, and proposer set are env-overridable here, while cadence lives
# only in the scheduler entry that invokes it. It never loops, sleeps, or
# retries — one invocation is one turn (see docs/paper_trading.md,
# "Scheduled Stage 1 Turns").
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

: "${CYCLE_SYMBOLS:=BTC-USD,ETH-USD}"
: "${CYCLE_GRANULARITY:=ONE_HOUR}"
: "${CYCLE_LOOKBACK:=200}"

exec uv run gpt-trader ideas cycle --from-coinbase \
  --symbols "${CYCLE_SYMBOLS}" \
  --granularity "${CYCLE_GRANULARITY}" \
  --lookback "${CYCLE_LOOKBACK}" \
  --format json
