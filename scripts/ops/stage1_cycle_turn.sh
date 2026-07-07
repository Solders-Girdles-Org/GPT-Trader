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

# Schedulers start jobs with a minimal PATH; make uv reachable whether it was
# installed by the Astral installer (~/.local/bin) or Homebrew.
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

# Same default universe as stage2_cycle_turn.sh: liquid Coinbase USD spot
# pairs whose quotes are all >= $1, so 0.01 price precision stays meaningful.
: "${CYCLE_SYMBOLS:=BTC-USD,ETH-USD,SOL-USD,XRP-USD,LTC-USD,LINK-USD,AVAX-USD,DOT-USD}"
: "${CYCLE_GRANULARITY:=ONE_HOUR}"
: "${CYCLE_LOOKBACK:=200}"
: "${CYCLE_PRICE_PRECISION:=0.01}"
# Space-separated proposer names; empty means the CLI default (all proposers).
: "${CYCLE_PROPOSERS:=}"

PROPOSER_FLAGS=()
for proposer in ${CYCLE_PROPOSERS}; do
  PROPOSER_FLAGS+=(--proposer "${proposer}")
done

# ${arr[@]+...} guards the empty-array expansion under set -u on bash 3.2
# (the macOS system bash that the launchd example invokes).
exec uv run gpt-trader ideas cycle --from-coinbase \
  --symbols "${CYCLE_SYMBOLS}" \
  --granularity "${CYCLE_GRANULARITY}" \
  --lookback "${CYCLE_LOOKBACK}" \
  --price-precision "${CYCLE_PRICE_PRECISION}" \
  ${PROPOSER_FLAGS[@]+"${PROPOSER_FLAGS[@]}"} \
  --format json
