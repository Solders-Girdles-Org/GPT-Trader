#!/usr/bin/env bash
# One bounded-autonomy (Stage 2) paper cycle turn, for launchd/cron entries.
#
# Unlike stage1_cycle_turn.sh (which only proposes and executes ideas a human
# already approved), this enables the audited Stage-2 gates and runs the full
# unattended loop: system-approve every violation-free proposal inside the
# budget envelope, then run one cycle turn so those approvals paper-execute
# against the turn's own snapshot (and expire + closeout-attribute the rest).
#
# It is paper-only and never contacts a live broker or account. Every approval
# and fill is still bounded by the audited bounded_autonomy mode and the
# versioned budget; each gate silently no-ops under any other mode, so a breach
# that ratchets autonomy down (daily-loss or drawdown-from-peak) halts
# auto-approval and auto-execution until the mode is re-earned. Enabling Stage 2
# is an operator act — see docs/paper_trading.md, "Bounded-autonomy turns
# (Stage 2)". One invocation is one turn; cadence lives only in the scheduler.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# Schedulers start jobs with a minimal PATH; make uv reachable whether it was
# installed by the Astral installer (~/.local/bin) or Homebrew.
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

# Liquid Coinbase USD spot pairs whose quotes are all >= $1, so the default
# --price-precision of 0.01 stays meaningful. Adding a sub-cent symbol requires
# a finer CYCLE_PRICE_PRECISION too (issue #1215: two symbols starved
# track-record depth; the busy-instrument skip means a wider set is what keeps
# proposals flowing every open-market turn).
: "${CYCLE_SYMBOLS:=BTC-USD,ETH-USD,SOL-USD,XRP-USD,LTC-USD,LINK-USD,AVAX-USD,DOT-USD}"
: "${CYCLE_GRANULARITY:=ONE_HOUR}"
: "${CYCLE_LOOKBACK:=200}"
: "${CYCLE_PRICE_PRECISION:=0.01}"
# Space-separated proposer names; empty means the CLI default (all proposers).
: "${CYCLE_PROPOSERS:=}"

# The two Stage-2 gates. Both must be enabled for the loop to auto-approve and
# auto-execute, and each still no-ops unless the audited autonomy mode resolves
# to bounded_autonomy at decision time
# (docs/decisions/stage2-auto-approval-workflow.md, stage2-execution-gate.md).
export GPT_TRADER_IDEAS_AUTO_APPROVAL=1
export GPT_TRADER_IDEAS_AUTO_EXECUTION=1

PROPOSER_FLAGS=()
for proposer in ${CYCLE_PROPOSERS}; do
  PROPOSER_FLAGS+=(--proposer "${proposer}")
done

# Approve eligible proposals from prior turns inside the budget envelope, then
# run the turn so those approvals execute against this turn's snapshot. The
# sweep is a no-op when nothing is eligible or the audited mode is not
# bounded_autonomy. Keep it non-fatal: the cycle turn is the streak evidence and
# must still append its manifest row even if the sweep step fails.
if ! uv run gpt-trader ideas approve --auto-sweep --format json; then
  echo "stage2: auto-approve sweep step failed (non-fatal); running the cycle turn" >&2
fi

# ${arr[@]+...} guards the empty-array expansion under set -u on bash 3.2
# (the macOS system bash that the launchd example invokes).
exec uv run gpt-trader ideas cycle --from-coinbase \
  --symbols "${CYCLE_SYMBOLS}" \
  --granularity "${CYCLE_GRANULARITY}" \
  --lookback "${CYCLE_LOOKBACK}" \
  --price-precision "${CYCLE_PRICE_PRECISION}" \
  ${PROPOSER_FLAGS[@]+"${PROPOSER_FLAGS[@]}"} \
  --format json
