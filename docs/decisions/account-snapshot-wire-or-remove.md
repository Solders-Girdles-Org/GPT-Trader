# Account snapshot command — wire a real provider or remove it

---
status: accepted
date: 2026-07-02
deciders: RJ
supersedes:
superseded-by:
---

## Context

`gpt-trader account snapshot` (`src/gpt_trader/cli/commands/account.py`) reads
`bot.account_telemetry`, which **no container wiring ever provides** — the
command fails with "Account snapshot telemetry is not available for this
broker" on every profile and broker, unconditionally. This was left as-is when
the never-constructed `CoinbaseAccountManager`/`AccountTelemetryService` stack
was removed
([remove-unwired-account-manager-and-strategy-lab](remove-unwired-account-manager-and-strategy-lab.md)),
with the note that a replacement needs a fresh spec. This record is that
decision point.

The stakes are no longer hypothetical: operator docs **instruct running the
command as a verification step** — `docs/production.md` uses
`account snapshot --profile observe` in three procedures, and
`docs/RUNBOOKS.md` uses it for "verify with broker" during incident response.
Those steps cannot succeed today; a responder following the runbook hits a
dead end at exactly the moment they need account truth.

## Options

- **Option A — Wire a minimal read-only snapshot provider.** A thin service
  over calls the engine already makes (`broker.list_balances`,
  `broker.list_positions`, ticker marks — the same surface
  `StateCollector.collect_account_state` uses), wired through the container
  and exposed to the CLI. Read-only; no order authority. Trade-off: new code
  to spec and maintain, but it un-breaks three documented operator
  procedures and the incident-response verification step with capabilities
  the codebase already possesses.
- **Option B — Remove the subcommand and scrub the docs.** Delete
  `account snapshot` (keeping `account diagnose`, which works), and rewrite
  the production/runbook steps around alternatives (`account diagnose` for
  credentials/API health, `run --dev-fast` status output for balances).
  Trade-off: smallest code surface, but incident responders lose the one-shot
  "what does the broker say my account holds" command, and the runbook
  rewrite substitutes weaker verification. Removal and docs scrub must land
  in one PR (docs link audit couples them).

## Decision

**Option A — wire a minimal read-only snapshot provider.** Accepted 2026-07-02
by RJ. The runbooks already treat an account snapshot as operationally
necessary, the data is one read-only call away on the existing broker surface,
and the staged-autonomy direction leans on transparency — a working snapshot
command is transparency infrastructure.

## Consequences

The spec issue is filed:
[#1121](https://github.com/Solders-Girdles/GPT-Trader/issues/1121) (provider
protocol, container wiring, CLI adaptation, unit tests over the real service
with a boundary-double broker), and the "not available" fallback is dropped
once wired.

## Safety boundary

Neither option authorizes order submission, execution enablement, or money
movement. Option A adds **read-only** broker API calls (balances, positions,
marks) behind the existing profile/credential gates; live-order authority
remains governed by `docs/DIRECTION.md`.
