#!/usr/bin/env bash
# One replay-evidence turn for the Stage 1 -> 2 scorecard (issue #1216).
#
# Builds a point-in-time snapshot of the paper universe from read-only public
# Coinbase candles, replays the active proposer set head-to-head against the
# deterministic baseline over that window (one tournament per symbol), and
# renders the scorecard with every tournament report attached. Replay results
# are reported as replay-labeled evidence alongside the wall-clock gates —
# never blended into them — so calibration, expectancy, and benchmark edge
# read now while wall-clock track-record depth accrues in parallel.
#
# Everything here is broker-free: no accounts are read and no orders are
# placed, modified, or canceled. Each run writes a self-contained evidence
# directory (snapshot, per-symbol tournament reports, scorecard artifact)
# under REPLAY_EVIDENCE_DIR so runs are comparable over time.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# Schedulers start jobs with a minimal PATH; make uv reachable whether it was
# installed by the Astral installer (~/.local/bin) or Homebrew.
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

# Defaults mirror the Stage-2 cycle turn (scripts/ops/stage2_cycle_turn.sh):
# same universe and granularity, so replay evidence measures the proposer set
# that is actually running, plus the strategy-backed convergence proposers
# (#1164) so benchmark edge is measured against genuine decision diversity,
# not only the overlay pair (#1245). The 720-candle hourly lookback (~30
# days) samples more regime variety than one 300-candle slice; the candle
# fetcher chunks requests, so the lookback is bounded by runtime (roughly
# 2.5 minutes per symbol at 720 candles x 4 proposers), not by the public
# API's 300-candle page size.
: "${REPLAY_SYMBOLS:=BTC-USD,ETH-USD,SOL-USD,XRP-USD,LTC-USD,LINK-USD,AVAX-USD,DOT-USD}"
: "${REPLAY_GRANULARITY:=ONE_HOUR}"
: "${REPLAY_LOOKBACK:=720}"
: "${REPLAY_PROPOSERS:=baseline-ma-10-50,regime-aware-ma-10-50,strategy-mean-reversion,strategy-regime-switcher}"
# Finer than the cycle's 0.01 default: the strategy adapter fails closed when
# quantization collapses stop/entry/target on low-priced symbols (DOT at the
# default grid), and replay evidence should measure those symbols, not drop
# them. Replay-only; live proposal records keep their own precision.
: "${REPLAY_PRICE_PRECISION:=0.0001}"
: "${REPLAY_EVIDENCE_DIR:=var/data/trade_ideas/replay_evidence}"

RUN_DIR="${REPLAY_EVIDENCE_DIR}/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${RUN_DIR}"

uv run gpt-trader ideas snapshot build --from-coinbase \
  --symbols "${REPLAY_SYMBOLS}" \
  --granularity "${REPLAY_GRANULARITY}" \
  --lookback "${REPLAY_LOOKBACK}" \
  --out "${RUN_DIR}/snapshot.json" \
  --format json >"${RUN_DIR}/snapshot_build.json"

REPORT_FLAGS=()
IFS=',' read -r -a SYMBOLS <<<"${REPLAY_SYMBOLS}"
for symbol in "${SYMBOLS[@]}"; do
  report="${RUN_DIR}/tournament_${symbol}.json"
  # A failed tournament writes its error envelope to the report path — kept
  # as run evidence, excluded from the scorecard render. One symbol must not
  # abort the turn: the other symbols' evidence still renders.
  if ! uv run gpt-trader ideas replay tournament \
    --file "${RUN_DIR}/snapshot.json" \
    --symbol "${symbol}" \
    --granularity "${REPLAY_GRANULARITY}" \
    --price-precision "${REPLAY_PRICE_PRECISION}" \
    --proposers "${REPLAY_PROPOSERS}" \
    --format json \
    --output "${report}"; then
    echo "replay_evidence: tournament failed for ${symbol}; error envelope kept at ${report}" >&2
    continue
  fi
  REPORT_FLAGS+=(--replay-report "${report}")
done

if [ ${#REPORT_FLAGS[@]} -eq 0 ]; then
  echo "replay_evidence: every tournament failed; no scorecard rendered (see ${RUN_DIR})" >&2
  exit 1
fi

exec uv run gpt-trader ideas scorecard "${REPORT_FLAGS[@]}" --output-dir "${RUN_DIR}"
