# Live Readiness and Gated Operations Guide

---
status: current
consolidates:
  - PRODUCTION_LAUNCH_CHECKLIST.md
  - PRODUCTION_DEPLOYMENT_RUNBOOK.md
  - PRODUCTION_ROLLOUT_PLAN.md
  - PRODUCTION_READINESS_IMPLEMENTATION.md
---

## Scope

This guide keeps the historical `production.md` path for link stability, but it
is not an approval to run unrestricted live automation. It describes the gates
and operator steps required before using live profiles or broker adapters.

Execution work should follow the
[Direction](DIRECTION.md). Treat
the existing `canary` and `prod` profiles as legacy live-operation assets:
useful for validation, but not proof that a product, account, or autonomy level
is ready. Per the accepted
[prod-canary-profile-meaning](decisions/prod-canary-profile-meaning.md)
decision, a profile name is configuration, never approval — the gates in
[DIRECTION.md](DIRECTION.md) and recorded human approval are the only
authorization path (test-enforced in
`tests/unit/gpt_trader/app/config/test_profile_not_execution_approval.py`).

## Operating Posture

- Start with `dev`, `paper`, or `observe` unless a live-operation gate has been
  explicitly passed.
- Use `human_approved_execution` as the first live execution tier.
- Verify product, account, venue/API, risk, and audit-log capability before
  enabling any new live lane.
- Keep broker-neutral decision and risk evidence canonical; broker-specific
  order tickets are downstream artifacts.
- Prefer `canary` for live validation and reserve `prod` for an explicitly
  approved, monitored live run.

## Readiness Checklist

### System

- [ ] Python 3.12+ and `uv` installed.
- [ ] Dependencies synced with `uv sync --all-extras --dev`.
- [ ] `.env` created from `config/environments/.env.template`.
- [ ] Working tree clean or local work preserved on a labeled branch.

### Venue And Product

- [ ] Coinbase CDP JWT credentials available for the selected lane.
- [ ] Product universe selected and supported by API/account capability.
- [ ] CFM futures enabled only after account/product verification (the only supported derivatives venue; INTX perpetuals were removed — see [decision record](decisions/intx-default-derivatives-venue.md)).
- [ ] Unsupported products remain research-only or ticket-draft only.

### Risk And Approval

- [ ] Daily loss limits and position caps reviewed.
- [ ] Reduce-only mode tested.
- [ ] Kill switch behavior understood.
- [ ] Human approval workflow selected for live submissions.
- [ ] Audit output location and retention confirmed.

## Validation Commands

```bash
# Canonical local gate (pr profile): the GitHub pull_request required surface
uv run local-ci

# Fast development loop: skips readiness, artifact freshness, optional suites
uv run local-ci --profile quick

# Strict/full: PR surface plus local/live readiness evidence
uv run local-ci --profile strict

# Profile and live-readiness diagnostics
uv run python scripts/production_preflight.py --profile canary
uv run python scripts/production_preflight.py --profile prod --warn-only

# Safe bot startup/shutdown baseline
uv run gpt-trader run --profile dev --dev-fast

# Operator surface
uv run gpt-trader account snapshot --profile observe
```

## Live Gate Sequence

### Gate 1: Observe

Use `observe` when you need real data and account visibility without execution.

```bash
uv run gpt-trader account snapshot --profile observe
```

Exit criteria:

- Account snapshot works.
- Market data freshness is acceptable.
- No order submission path is enabled.

### Gate 2: Canary Dry Run

Use `canary --dry-run` to validate profile settings and runtime behavior without
exchange orders.

```bash
uv run gpt-trader run --profile canary --dry-run
```

Exit criteria:

- Preflight report is current.
- Runtime logs and status output are clean.
- Risk guards produce expected decisions.
- Operator can stop the process and inspect state.

### Gate 3: Human-Approved Canary

Use canary only after the decision framework has recorded product scope,
approval policy, risk budget, and audit expectations.

```bash
uv run gpt-trader run --profile canary --reduce-only
```

Exit criteria:

- Every submitted order has a corresponding approval and audit trail.
- Reduce-only behavior is verified before any position-increasing flow.
- Daily loss and exposure limits match the active lane.

### Gate 4: Approved Live Profile

The `prod` profile is a live-operation profile, not a default destination. Use it
only after canary evidence supports the same product lane and autonomy tier.

```bash
uv run gpt-trader run --profile prod --dry-run
uv run gpt-trader run --profile prod --reduce-only
```

Do not remove `--dry-run` or `--reduce-only` unless the current run has explicit
approval and monitoring coverage.

## Monitoring

Use the [Monitoring Playbook](MONITORING_PLAYBOOK.md) and
[Observability Reference](OBSERVABILITY.md) for metric names and dashboards.

Key signals:

- Position count and exposure.
- Daily realized and unrealized PnL.
- Order rejection and retry rates.
- Guard trip counts.
- WebSocket and market-data staleness.
- API latency and rate-limit behavior.

Suggested live thresholds:

| Signal | Warning | Critical |
|--------|---------|----------|
| Daily loss | > configured warning budget | hard stop at configured daily limit |
| Order error rate | > 10% | > 15% |
| WebSocket staleness | > 30s | > 60s |
| Broker latency p95 | > 1000 ms | > 3000 ms |

## Rollback

If live behavior is not explainable or expected:

```bash
# 1. Force reduce-only behavior
export RISK_REDUCE_ONLY_MODE=1

# 2. Stop new automation
pkill -f gpt-trader

# 3. Inspect current account and orders
uv run gpt-trader account snapshot --profile observe
sqlite3 runtime_data/prod/orders.db "select status, count(*) from orders group by status;"

# 4. Review recent errors
tail -n 1000 ${COINBASE_TRADER_LOG_DIR:-var/logs}/coinbase_trader.log | grep ERROR
```

Use the broker UI or explicitly approved CLI previews for manual exits. Do not
assume the bot can safely recover an unknown state without human review.

## Emergency Stop

```bash
export RISK_KILL_SWITCH_ENABLED=1
export RISK_REDUCE_ONLY_MODE=1
pkill -f gpt-trader
```

After emergency stop:

1. Verify positions directly with the broker.
2. Save current status, logs, and order database snapshots.
3. Record root cause and affected product lane.
4. Restart only from `observe` or `canary --dry-run`.

## Golden Rules

1. Existing code paths are not approval to trade.
2. Product coverage is constrained by verified venue/API/account capability.
3. Live order submission starts with human approval.
4. A run without audit evidence does not count as readiness evidence.
5. Unknown state means stop, observe, and reconcile before continuing.
