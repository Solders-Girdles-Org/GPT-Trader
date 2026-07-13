---
status: current
workstream: 1 (see TRADE_IDEA_INTERFACES_DESIGN_NOTES.md)
depends-on: Workstream 0 (implemented service factory, error codes, actor resolution)
---

# Implemented Spec: `gpt-trader ideas` CLI Command Group

## Goal

A complete, scriptable CLI surface over `TradeIdeaService` so that AI agents
can propose trade-idea records and humans can review them, with JSON output
for programmatic use. This is the first working door into the staged-autonomy
workflow and is implemented in `src/gpt_trader/cli/commands/ideas.py`.

This document is now a maintainer reference for the implemented CLI contract,
not a request to build a new command group. Future work should update the
current command and tests rather than duplicating the trade-ideas interface.

## Files

| File | Action |
|------|--------|
| `src/gpt_trader/cli/commands/ideas.py` | Implemented command group |
| `src/gpt_trader/cli/__init__.py` | Registers `ideas.register(subparsers)` alongside existing commands |
| `src/gpt_trader/cli/response.py` | Defines `POLICY_VIOLATION`, `IDEA_NOT_FOUND` in `CliErrorCode` |
| `src/gpt_trader/features/trade_ideas/service.py` | Defines `DEFAULT_IDEAS_ROOT`, `create_trade_idea_service()` factory |
| `tests/unit/gpt_trader/cli/commands/test_ideas_*.py` | Implemented CLI tests split by lifecycle/query/budget and focused integrity cases |

The implementation follows the structure of `cli/commands/controls.py`: a
`register(subparsers)` function, one `_handle_*` per subcommand returning
`CliResponse`, with standard text/json output options on every subcommand and
CSV enabled only on report/export commands that document a tabular contract.

## Common options

Every storage-backed workflow subcommand accepts:

- `--format {text,json}` unless the command documents additional artifact
  formats
- `--ideas-root PATH` — override storage root (default: `GPT_TRADER_IDEAS_ROOT`
  env, then `var/data/trade_ideas/`)

Exception: `export-ticket` defaults `--format` to raw canonical JSON and
supports `--out PATH` / `--output PATH` / `-o PATH` for writing the ticket
artifact itself instead of a `CliResponse` envelope.

Read-only replay commands accept `--format {text,json}` but do not read or
write `--ideas-root`; they operate only on the local fixture named by `--file`.

Every mutating subcommand accepts:

- `--actor ID` — actor identity (default: `GPT_TRADER_ACTOR` env, then OS user)

Exception: `propose-baseline` defaults to the deterministic baseline proposer
id so generated proposal audit events identify the proposer unless an operator
explicitly overrides `--actor`.

Current behavior is discoverable with:

```bash
uv run gpt-trader ideas --help
```

Reporting/export commands add machine artifact formats where documented below:

- `ideas report --format {text,json,csv}`
- `ideas audit export --format {json,csv}`
- `ideas closeout export --format {json,csv}`
- `--output PATH` writes the command output through the standard CLI output
  convention; `--output-dir DIR` writes a durable artifact named from the
  deterministic artifact id while still returning the command response.

## Command tree

```
gpt-trader ideas
├── propose          --file PATH | --stdin   [--actor-type {ai,human}] [--reason TEXT]
├── propose-baseline --snapshot PATH         [--actor ID] [--reason TEXT]
├── snapshot
│   └── build        --from-coinbase --symbols BTC-USD,ETH-USD
│                    --granularity GRANULARITY --lookback N --out snapshot.json
│                    [--as-of TIMESTAMP] [--source-label LABEL]
├── resubmit         --file PATH | --stdin   [--actor-type {ai,human}] [--reason TEXT]
├── list             [--state STATE] [--instrument SYMBOL] [--decision-id ID]
│                    [--direction DIRECTION]
│                    [--min-confidence LABEL] [--max-confidence LABEL]
│                    [--updated-since TS] [--updated-until TS]
│                    [--sort-by FIELD] [--descending]
│                    [--limit N] [--offset N]
├── show             DECISION_ID [--events]
├── report           [--since ISO|YYYY-MM-DD] [--until ISO|YYYY-MM-DD]
│                    [--output PATH] [--output-dir DIR] [--format text|json|csv]
├── export-ticket    --decision-id ID --venue {coinbase,manual,robinhood}
│                    [--venue-order-type TEXT] [--time-in-force TEXT]
│                    [--client-order-id ID] [--out PATH] [--format {json,text}]
├── replay
│   └── baseline     --file PATH --symbol SYMBOL --granularity GRANULARITY
│                    [--short-window N] [--long-window N]
│                    [--crossover-lookback N] [--min-history N]
│                    [--risk-per-idea-pct DECIMAL] [--entry-band-pct DECIMAL]
│                    [--reward-multiple DECIMAL] [--expiry-hours N]
│                    [--expected-hold TEXT] [--price-precision DECIMAL]
│                    [--source LABEL]
├── closeout
│   ├── record       DECISION_ID --resolution {thesis_target,invalidation,expiry}
│   │                [--realized-profit-loss-amount DECIMAL]
│   │                [--realized-profit-loss-percent DECIMAL]
│   │                [--realized-profit-loss-unavailable-reason TEXT]
│   │                [--evidence TEXT]... [--actor-type {human,system}]
│   ├── list         [--decision-id ID] [--resolution RESOLUTION] [--actor ID]
│   │                [--actor-type {ai,human,system,venue}]
│   │                [--has-evidence true|false] [--since ISO|YYYY-MM-DD]
│   │                [--until ISO|YYYY-MM-DD] [--limit N] [--offset N]
│   ├── export       same filters as list [--format json|csv] [--output PATH]
│   │                [--output-dir DIR]
│   └── show         DECISION_ID
├── approve          DECISION_ID --reason TEXT | --auto-sweep
├── reject           DECISION_ID --reason TEXT
├── request-changes  DECISION_ID --reason TEXT
├── cancel           DECISION_ID --reason TEXT
├── expire           DECISION_ID [--reason TEXT] | --sweep
├── mark-submitted   DECISION_ID --venue {coinbase,manual} [--external-order-id ID] [--reason TEXT]
├── mark-filled      DECISION_ID --venue {coinbase,manual} [--external-order-id ID] [--reason TEXT]
├── budget
│   ├── show
│   └── set          --reason TEXT [one flag per RiskBudget field]
├── autonomy
│   ├── show
│   ├── history
│   └── set          --mode {research_only,human_approved_execution,bounded_autonomy}
│                    --reason TEXT
└── audit
    ├── list         [--decision-id ID] [--actor ID]
    │                [--actor-type {ai,human,system,venue}]
    │                [--action ACTION] [--state STATE]
    │                [--since ISO|YYYY-MM-DD] [--until ISO|YYYY-MM-DD]
    │                [--limit N] [--offset N]
    ├── export       same filters as list [--format json|csv] [--output PATH]
    │                [--output-dir DIR]
    ├── tail         [-n N] [--decision-id ID]
    └── verify
```

### `ideas propose`

- Input: a `TradeIdea` JSON document (the `to_dict` shape from
  `features/trade_ideas/models.py`) from `--file` or stdin. `--file` and
  `--stdin` are mutually exclusive; one is required.
- Parse via `TradeIdea.from_dict`. `KeyError`/`ValueError` →
  `INVALID_ARGUMENT` naming the missing/invalid field.
- Call `service.propose(idea, actor_id=..., actor_type=...)`.
- On success, `data` contains `decision_id`, `state`, `record_hash`, and the
  eligibility preview: `violations` from
  `ApprovalPolicy().approval_violations(...)` evaluated read-only with the
  current budget, so the proposer immediately sees whether the idea could be
  approved as-is. Preview violations are `warnings`, not errors — propose
  still succeeds (rejection happens at review, per the framework).
- Text: `✓ ideas propose OK (trade-20260612-001, state=proposed)` followed by
  `⚠ would fail approval: <reason>` lines if any.

### `ideas propose-baseline`

- Input: a local `MarketSnapshot` JSON fixture from `--snapshot PATH`.
- Never calls broker, account, credential, preflight, canary, or live market
  surfaces. The snapshot file is the complete data source for the command.
- Parse the fixture into `MarketSnapshot` / `SymbolSeries`, run
  `BaselineProposer`, preflight every emitted `TradeIdea` with
  `service.validate_new_proposal`, preview approval policy with
  `service.approval_violations`, then persist through
  `service.propose(..., actor_type=ai)`.
- The default actor id is the deterministic proposer id
  (`baseline-ma-10-50` today); `--actor` overrides it when an operator needs a
  different audit identity.
- No-signal snapshots return success with `proposal_count=0` and
  `was_noop=true`; they do not create trade-idea records.
- Duplicate decision ids fail before any proposed record or audit event is
  written for that invocation.
- JSON `data` contains `proposer_id`, snapshot metadata, `proposal_count`, and
  one entry per proposal:

  ```json
  {
    "decision_id": "trade-20350612-btcusd-4c5a9e2d",
    "state": "proposed",
    "instrument": "BTC-USD",
    "direction": "long",
    "record_hash": "sha256...",
    "approval_preview": {
      "violations": [],
      "warnings": []
    }
  }
  ```

  Approval-preview warnings are also mirrored into the top-level
  `CliResponse.warnings` list for agent callers that already consume warning
  envelopes.

#### `propose-baseline` snapshot fixture shape

The JSON file uses strings for timestamps and decimal fields so fixtures remain
stable across languages and shells:

```json
{
  "as_of": "2035-06-12T00:00:00+00:00",
  "source": "local-fixture:coinbase-candles",
  "series": [
    {
      "symbol": "BTC-USD",
      "granularity": "1d",
      "candles": [
        {
          "ts": "2035-04-20T00:00:00+00:00",
          "open": "100",
          "high": "100",
          "low": "100",
          "close": "100",
          "volume": "1000"
        }
      ]
    }
  ]
}
```

All candle timestamps must include a timezone, be strictly ascending within a
series, and be strictly before `as_of`. The command rejects malformed fixtures
as `INVALID_ARGUMENT`.

### `ideas snapshot build`

- Output: a local `MarketSnapshot` JSON file that can be passed directly to
  `ideas propose-baseline --snapshot PATH`.
- The live market fetch path is explicit: `--from-coinbase` is required. The
  command builds from read-only public Coinbase market candles through the
  existing historical candle abstractions; it never reads accounts, performs
  product/account discovery, runs broker readiness checks, preflight, canary, or
  order-affecting commands.
- Required options:
  - `--from-coinbase`
  - `--symbols BTC-USD,ETH-USD` (comma-separated, unique Coinbase product ids)
  - `--granularity GRANULARITY`
  - `--lookback N` (number of completed candles to include per symbol)
  - `--out PATH`
- Optional options:
  - `--as-of TIMESTAMP` (timezone required; defaults to current UTC time)
  - `--source-label LABEL` (default `coinbase:market-candles`)
  - `--coinbase-base-url URL` (default `https://api.coinbase.com`)
- Point-in-time rules:
  - Fetch start is aligned from the last closed candle boundary:
    `[last_closed_boundary - granularity * lookback, as_of)`.
  - Source candles must be strictly ascending by timestamp.
  - Candle selection includes only fully closed bars: for each source candle,
    `candle.ts + granularity <= as_of`. Candles that start before `as_of` but
    are still open are skipped so current or future bars cannot leak into
    proposer input.
  - Each configured symbol must have at least one completed candle in the
    window.
- JSON `data` contains `out` plus snapshot metadata: `as_of`, `source`,
  `symbols`, and per-series `candle_count`, `first_ts`, and `last_ts`.
- Text starts with:

  ```text
  ✓ ideas snapshot build OK (2 series -> var/snapshots/coinbase.json)
  ```

Recommended Stage 1 workflow:

```bash
uv run gpt-trader ideas snapshot build \
  --from-coinbase \
  --symbols BTC-USD,ETH-USD \
  --granularity ONE_HOUR \
  --lookback 53 \
  --out var/snapshots/coinbase-market-snapshot.json

uv run gpt-trader ideas propose-baseline \
  --snapshot var/snapshots/coinbase-market-snapshot.json \
  --format json
```

For replay calibration, keep using `ideas replay baseline --file` with a local
candle fixture. The snapshot build output is the one-shot point-in-time proposer
input; replay consumes a longer historical candle series and constructs many
point-in-time snapshots internally.

### `ideas resubmit`

Same input handling as `propose`; calls `service.resubmit`. The record must
already exist (`IDEA_NOT_FOUND` otherwise). Service/audit layer enforces the
`needs_changes → proposed` transition; surface `InvalidTransitionError` as
`VALIDATION_ERROR`.

### `ideas list`

- Calls `service.list_view_result(TradeIdeaListQuery(...))` so CLI commands
  share filter, sort, and pagination semantics through the service.
- Filters:
  - `--state` choices = `TradeIdeaState` values.
  - `--instrument` exact instrument match, case-insensitive.
  - `--decision-id` exact trade idea id.
  - `--direction` choices = `TradeDirection` values.
  - `--min-confidence` / `--max-confidence` confidence-label range, ordered
    `low < medium < high`.
  - `--updated-since` / `--updated-until` compare the latest audit event
    timestamp.
- Sorting: `--sort-by {decision_id,state,instrument,direction,confidence,
  max_loss_pct,expires_at,created_at,updated_at}` with optional
  `--descending`. Without `--sort-by`, existing store decision-id order is
  preserved for backward-compatible text output.
- Pagination: `--limit` / `--offset`. JSON `data` includes `total_count`,
  `returned_count`, `offset`, `limit`, and `has_more` alongside `ideas`.
- Text: aligned table — `DECISION_ID  STATE  INSTRUMENT  DIRECTION
  MAX_LOSS%  EXPIRES_AT`. JSON: `data["ideas"]` = list of
  `{decision_id, state, instrument, direction, max_loss_pct, expires_at,
  confidence}`.
- Empty store is success with an empty list, not an error.

### `ideas show DECISION_ID`

- Calls `service.get`. `data` = full `idea.to_dict()` plus `state`.
- `--events` additionally includes the audit history
  (`[e.to_dict() for e in view.events]`).
- Text: field-per-line record rendering; with `--events`, a chronological
  `timestamp  actor_type/actor_id  action  before→after  reason` table.

### `ideas report`

- Read-only track-record report over the local `--ideas-root` records, audit
  events, and closeout attribution log. It does not call broker, account,
  venue, preflight, canary, or live-trading surfaces.
- Empty stores return success with zero counts and `was_noop=True`.
- `--since/--until` (aliases `--from/--to`) filter the report by proposal
  timestamp. Date-only values are interpreted as UTC midnight.
- JSON output is a stable artifact payload with `schema_version`,
  `quality_report_id`, `generated_at`, `filters`, `row_count`, `source`, and
  the existing `proposal_volume`, `workflow`, `quality`, and `closeouts`
  sections. `quality_report_id` is deterministic for the same data and window.
- CSV output is a flat metric table with columns
  `quality_report_id,schema_version,metric_path,value`.
- `--output-dir DIR` writes `trade-idea-report-<quality_report_id>.json` or
  `.csv` in addition to the normal command response.
- JSON `data` contains:
  - `proposal_volume`: idea count, proposal event count, resubmission count,
    and monthly volume/approval/closeout buckets.
  - `workflow`: lifecycle event counts, current-state counts, ever-approved /
    submitted / filled counts, and rates.
  - `quality`: eligibility and approval-policy quality counts evaluated
    read-only against stored records and the default risk-budget constants,
    without seeding `risk_budget.jsonl`.
  - `closeouts`: terminal closeout coverage, missing terminal closeout ids,
    resolution counts, profit/loss outcome distribution, and realized P/L
    versus max-loss comparisons when closeout records include numeric data.
- Text starts with:

  ```text
  ✓ ideas report OK (3 ideas, approval_rate=33.33%, closeout_coverage=66.67%)
  ```

  When `proposal_volume.by_month` has buckets, text output also includes a
  compact `Monthly` section with one line per month showing idea count, approval
  rate, closeout coverage, and realized P/L amount. Empty stores and reports
  without monthly buckets omit the section while still returning success.

### `ideas export-ticket`

- Render-only command that exports a deterministic broker-neutral ticket JSON
  artifact from local trade-idea records and audit state. It never calls broker,
  account, credential, preflight, canary, order-preview, or live-trading
  surfaces, and it does not mutate the persisted `TradeIdea.broker_ticket`.
- Required options:
  - `--decision-id ID`
  - `--venue {coinbase,manual,robinhood}`. Robinhood is render-only and is
    deliberately absent from lifecycle-recording commands.
- Optional venue placeholders:
  - `--venue-order-type TEXT` (default `operator_selected`)
  - `--time-in-force TEXT` (default `operator_selected`)
  - `--client-order-id ID` (defaults deterministically from venue, decision id,
    and record hash; long decision ids are hash/truncated so generated ids stay
    within the same 128-character validation bound as explicit ids)
- Output:
  - Default success output is the raw canonical ticket JSON on stdout, not a
    `CliResponse` envelope. Canonical JSON uses sorted keys and stable
    formatting, so exports are byte-stable for the same audited state and fixed
    export/evaluation time. Re-exporting at a different wall-clock instant
    intentionally changes `policy_budget_snapshot.evaluated_at`, the raw bytes,
    and `ticket_hash`.
  - `--out PATH` / `--output PATH` / `-o PATH` writes that same canonical JSON
    artifact to a file instead of stdout. With default JSON output, successful
    file writes are quiet; with `--format text`, the summary includes a
    `written_to: PATH` confirmation line.
  - Errors still use the normal CLI error response conventions.
- State guard: export succeeds only for `approved`, `submitted`, `filled`,
  `cancelled`, or `expired` ideas that have an approval event. It fails with
  `VALIDATION_ERROR` for unapproved states such as `proposed`,
  `needs_changes`, `rejected`, or pre-approval `expired`. Approved ideas also
  recheck `time_horizon.expires_at` at export time and fail instead of
  producing an actionable ticket when the idea has gone stale.
  Robinhood export is narrower: it succeeds only while the idea is `approved`,
  so the artifact cannot claim a Robinhood submitted or filled state.
- Payload includes:
  - `schema_version`, `decision_id`, `record_hash`, deterministic
    `generated_at`, and `ticket_hash`.
  - `decision_metadata` with autonomy mode, instrument, product type,
    direction, confidence, and thesis.
  - `risk_sizing_snapshot` with entry zone, max loss, and sizing
    recommendation.
  - `timing_invalidation_constraints` with horizon, invalidation, target exit,
    failure mode, and do-not-trade conditions.
  - `policy_budget_snapshot` with the current persisted/default risk budget and
    approval-policy violations evaluated at the export/evaluation timestamp
    recorded as `policy_budget_snapshot.evaluated_at`. Its `autonomy_mode` (and
    `autonomy_mode_source`) is the resolved active level the violations were
    evaluated against, which can differ from the mode the idea claimed at
    proposal time (`decision_metadata.autonomy_mode` preserves the record
    field).
  - `broker_ticket.source_record` copied from the immutable `TradeIdea` plus a
    derived broker-neutral export ticket.
  - `venue_request`, `venue_payload`, and sanitized provenance for created,
    approval, latest, and terminal audit events. External order ids are not
    copied into the artifact; provenance records only whether one was present.

Example:

```bash
uv run gpt-trader ideas export-ticket \
  --decision-id trade-20350612-btcusd-4c5a9e2d \
  --venue robinhood \
  --venue-order-type operator_selected \
  --time-in-force operator_selected \
  --out review_artifacts/tmp/trade-ticket.json
```

### `ideas replay baseline`

- Read-only Stage 1 calibration command over local candle fixtures. It
  constructs `BaselineProposer(BaselineProposerConfig(...))`, feeds it through
  `TradeIdeaReplayRunner`, and formats the returned `ReplayReport`.
- It never reads credentials, contacts brokers/accounts/live market data, writes
  trade-idea records, creates tickets, or touches closeout attribution.
- Required options:
  - `--file PATH`
  - `--symbol SYMBOL`
  - `--granularity GRANULARITY`
- Supported baseline/replay options:
  - `--source LABEL` (default `fixture:candles`)
  - `--min-history N` (default `max(short-window, long-window) + crossover-lookback`)
  - `--short-window N` (default `10`)
  - `--long-window N` (default `50`)
  - `--crossover-lookback N` (default `3`)
  - `--risk-per-idea-pct DECIMAL` (default `2`)
  - `--entry-band-pct DECIMAL` (default `1`)
  - `--reward-multiple DECIMAL` (default `2`)
  - `--expiry-hours N` (default `48`)
  - `--expected-hold TEXT` (default `5-15 days`)
  - `--price-precision DECIMAL` (default `0.01`)
- Candle fixture shape:

  ```json
  {
    "candles": [
      {
        "ts": "2026-06-12T12:00:00+00:00",
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "1000"
      }
    ]
  }
  ```

  Timestamps are ISO-8601; naive timestamps are treated as UTC. Decimal values
  may be strings or JSON numbers. Malformed JSON, a missing `candles` array,
  missing candle fields, invalid timestamps, non-finite decimals, and invalid
  OHLCV rows (`high < low`, open/close outside `[low, high]`, or negative
  volume) return `INVALID_ARGUMENT` with the offending field when available.
- JSON `data` is `ReplayReport.to_dict()` and includes `proposer_id`,
  `symbol`, `granularity`, `source`, `snapshots_evaluated`, `ideas_proposed`,
  `target_hits`, `stop_hits`, `timed_out`, `not_filled`, `no_future_data`,
  `resolved_ideas`, `target_hit_rate`, `stop_hit_rate`, `average_return_r`, and
  per-idea replay outcomes.
- Text starts with:

  ```text
  ✓ ideas replay baseline OK (BTC-USD ONE_HOUR, snapshots=2, ideas=1)
  proposer_id: baseline-ma-2-4
  outcomes: target_hits=1, stop_hits=0, timed_out=0, not_filled=0, no_future_data=0
  hit_rates: target=100.00%, stop=0.00%
  average_return_r: 2
  ```

  A replay with zero proposed ideas succeeds with `was_noop=True`.

### `ideas closeout record` / `ideas closeout show` / `ideas closeout list` / `ideas closeout export`

- `record` wraps `service.record_closeout_attribution`; `show` wraps
  `service.get_closeout_attribution`. Both use only local trade-idea storage.
- `record` requires a terminal idea. The service enforces the terminal-state
  precondition and pins the attribution to the latest terminal audit event and
  record hash.
- `record` accepts:
  - `--resolution {thesis_target,invalidation,expiry}`
  - `--realized-profit-loss-amount DECIMAL` and/or
    `--realized-profit-loss-percent DECIMAL` (negative values represent losses)
  - `--realized-profit-loss-unavailable-reason TEXT` when numeric realized P/L
    is unavailable
  - repeated `--evidence TEXT` strings
  - `--actor-type {human,system}` with default `human`
- At least one realized P/L value or unavailable reason is required.
- JSON `data` for both successful commands is
  `{decision_id, closeout_attribution}`. `closeout_attribution` is the
  persisted closeout record dictionary, or `null` for `show` when the idea is
  known but has no attribution (`was_noop=True`).
- Text starts with:

  ```text
  ✓ ideas closeout record OK (trade-20260612-001, resolution=thesis_target)
  ```

- These commands never call broker, account, venue, preflight, canary, ticket
  payload-generation, or live-trading surfaces. Evidence strings are operator
  references only.
- `list` and `export` read the append-only closeout attribution log without
  mutating records. Filters: `--decision-id`, `--resolution`, `--actor`,
  `--actor-type`, `--has-evidence`, `--since`, `--until`, `--limit`, and
  `--offset`.
- `list` returns a paginated JSON payload with `schema_version`, `filters`,
  `pagination`, and `closeouts`. Rows join the stored closeout record with the
  terminal audit event fields (`terminal_event_timestamp`, `terminal_action`,
  `terminal_state`) only for reporting/evidence; stored records are not
  rewritten.
- `export --format json` returns the same row shape inside an artifact envelope
  with `closeout_export_id`, `artifact_type`, `generated_at`, and `row_count`.
  `export --format csv` returns one row per closeout attribution with joined
  terminal-event columns.

### `ideas approve DECISION_ID --reason TEXT`

- `actor_type` hard-coded `HUMAN`. `--reason` required and non-empty.
- `PolicyViolationError` → exit 1, code `POLICY_VIOLATION`,
  `data["violations"]` = full list. Text mode:

  ```
  ✗ ideas approve FAILED: approval refused (3 violations)
    - max_loss 8% exceeds budget cap of 5% per idea
    - 2 tickets already approved; budget allows 2 concurrent approved tickets
    - Idea expired at 2026-06-11T20:00:00+00:00; approve nothing stale
  ```

### `ideas approve --auto-sweep`

Stage 2 auto-approval inside the budget envelope
(`docs/decisions/stage2-auto-approval-workflow.md`) over
`service.auto_approve_sweep()`.

- Doubly gated, both refusals loud (`POLICY_VIOLATION`, never a silent no-op):
  the `GPT_TRADER_IDEAS_AUTO_APPROVAL` feature flag must be explicitly enabled
  (default off; environment-only, no argument override), and the audited
  autonomy log must resolve to `bounded_autonomy`.
- Sweeps `proposed` ideas oldest-review-first; each idea is re-checked against
  the full approval policy with `actor_type=SYSTEM` and the mode is re-resolved
  per decision so the daily-loss ratchet can halt a sweep mid-pass.
- Zero violations → audited `approved` event with system actor
  (`auto-approval-sweep`), an `auto-approval:` prefix plus a following space,
  and budget-envelope evidence (autonomy version, budget version, exposure
  numbers). Any violation → the idea stays `proposed` and is reported under
  `data["skipped"]` with its violations, with an audited
  `auto_approval_skipped` event preserving the proposed state.
- `data` = `evaluated_at`, `autonomy_mode`, `autonomy_version`, `counts`,
  `approved` (view summaries), `skipped` (decision_id + violations).
  `was_noop=True` when the proposed queue is empty.
- DECISION_ID and `--reason` are refused alongside `--auto-sweep`
  (`INVALID_ARGUMENT`); the sweep records its own audited reasons.
- Manual trigger only — no scheduler ships with this surface.
- Paper execution of system approvals is separately gated by
  `docs/decisions/stage2-execution-gate.md`: the latest approval must be the
  auto-sweep system actor, `GPT_TRADER_IDEAS_AUTO_EXECUTION` must be enabled,
  and the audited autonomy log must resolve to `bounded_autonomy` at execution
  time. Without both gates, `execute-paper` and `ideas cycle` preserve the
  previous typed refusal / audited skip behavior.

### `ideas execute-paper`

- Executes one approved idea against the offline deterministic paper broker and
  records the `submitted`/`filled` lifecycle through `PaperIdeaExecutor`.
- Human-approved ideas execute as before. System-approved ideas execute only
  under the Stage 2 execution gate (`GPT_TRADER_IDEAS_AUTO_EXECUTION` enabled plus
  audited `bounded_autonomy`, latest approval actor `auto-approval-sweep`).
- No live broker/account/API is reachable from this command; the paper broker
  type allowlist remains the execution boundary.

### `ideas reject` / `request-changes` / `cancel`

Thin wrappers over `service.reject` / `service.request_changes` /
`service.cancel` with `actor_type=HUMAN` and required `--reason`.

### `ideas expire`

- `ideas expire DECISION_ID` — expire one idea (service defaults for
  reason/actor unless overridden).
- `ideas expire --sweep` — list all non-terminal views and expire each idea
  that can legally transition to expired when either:
  - `time_horizon.expires_at` is set and `<= now`; or
  - the review deadline has elapsed under the current
    `RiskBudget.max_review_latency_hours` policy.
  Review latency consumes wall-clock time for 24x7 crypto and open XNYS time
  for equities. A due equity idea is not mutated while XNYS is closed; the
  authored `time_horizon.expires_at` remains unchanged and the next open sweep
  may expire it. An unclassifiable or unavailable session fails closed without
  an expiry mutation. Queue status continues to show the immutable hard
  horizon timestamp even when a closed equity session delays the sweep itself.
  The review-latency path can expire a far-future idea whose `expires_at` is
  still later than `now` when the review queue exceeds the configured budget.
  Report `data["expired"]` = list of decision_ids; success even when zero
  matched (`was_noop=True`). DECISION_ID and `--sweep` are mutually exclusive;
  one is required.

### `ideas mark-submitted` / `ideas mark-filled`

- Wrap `service.record_submission` / `service.record_fill`.
- **These record manually executed tickets; they never touch a broker API.**
  Help text says so, and this boundary must remain explicit in future changes.
- `--venue` choices: `coinbase`, `manual` (no INTX-specific venue surface).

### `ideas budget show` / `ideas budget set`

- `show`: `data` = `service.current_budget().to_dict()` (this seeds defaults
  on first use — acceptable and documented).
- `set`: one optional flag per `RiskBudget` field
  (`--max-loss-per-idea-pct`, `--max-daily-loss-pct`,
  `--max-open-notional-pct`, `--max-concurrent-approved-tickets`,
  `--max-review-latency-hours`, `--sizing-capped-by-budget {true,false}`,
  `--gain-retention-floor-pct`, `--allow-futures-leverage {true,false}`,
  `--allow-naked-shorts {true,false}`, `--account-equity`,
  `--max-drawdown-from-peak-pct`, `--max-equity-buying-power-pct`),
  plus required `--reason`. Build the
  new `RiskBudget` by copying the current one with overrides and
  `version = current.version + 1`; call
  `service.update_budget(budget, ActorType.HUMAN, actor_id)`.
  At least one field flag is required (`MISSING_ARGUMENT` otherwise).
  `PolicyViolationError` → `POLICY_VIOLATION`.

### `ideas autonomy show` / `ideas autonomy history` / `ideas autonomy set`

Implements the CLI surface from
`docs/decisions/persistent-autonomy-state.md` over the append-only
`autonomy_state.jsonl` beside the budget log.

- `show`: `data` = the resolved autonomy level with provenance (`mode`,
  `version`, `source`, `error`), plus the latest entry's actor, rationale, and
  evidence when the log has entries. Read-only: it never seeds the log. An
  absent log reports the seeded default `human_approved_execution`
  (`source=seeded_default`); an unreadable or integrity-broken log reports
  `research_only` with `source=fail_closed` and the surfaced error — the
  command still exits 0 because the resolved state is the truthful answer.
- `history`: every audited autonomy-level version, oldest first, including
  the breach evidence carried by automatic ratchet entries.
  `AutonomyIntegrityError` → `OPERATION_FAILED`.
- `set`: `service.set_autonomy_mode(mode, actor_type=HUMAN, actor_id, reason)`
  with required `--mode` and `--reason`. Raising (or re-affirming) the level
  requires a human actor; lowering is open to any actor so the automatic
  ratchet can act. `PolicyViolationError` → `POLICY_VIOLATION`; a broken log
  refuses the change with `OPERATION_FAILED`.

### `ideas audit list` / `ideas audit export` / `ideas audit tail` / `ideas audit verify`

- `list`: paginated filtered audit events from `service.audit_log.read_events`.
  Filters: `--decision-id`, `--actor`, `--actor-type`, `--action`, `--state`,
  `--since`, `--until`, `--limit`, and `--offset`.
- `export --format json`: machine artifact envelope with `schema_version`,
  `audit_export_id`, `artifact_type`, `generated_at`, `filters`, `pagination`,
  `row_count`, and `rows`.
- `export --format csv`: tabular audit events with event id, timestamp,
  decision id, actor, action, state transition, reason, record hash, evidence,
  venue, and external order id.
- `tail`: last N events (default 20) from
  `service.audit_log.read_events(decision_id)`, newest last.
- `verify`: full read of the audit log; `AuditIntegrityError` →
  `✗ ideas audit verify FAILED: <reason>` with `OPERATION_FAILED`. Success
  reports event count: `✓ ideas audit verify OK (142 events)`.

### Automated evidence examples

Weekly report artifact:

```bash
uv run gpt-trader ideas report \
  --ideas-root var/data/trade_ideas \
  --from 2026-06-01 \
  --to 2026-06-07T23:59:59Z \
  --format json \
  --output-dir review_artifacts/trade_ideas
```

Monthly audit and closeout CSV exports:

```bash
uv run gpt-trader ideas audit export \
  --ideas-root var/data/trade_ideas \
  --from 2026-06-01 \
  --to 2026-06-30T23:59:59Z \
  --format csv \
  --output review_artifacts/trade_ideas/audit-2026-06.csv

uv run gpt-trader ideas closeout export \
  --ideas-root var/data/trade_ideas \
  --from 2026-06-01 \
  --to 2026-06-30T23:59:59Z \
  --format csv \
  --output review_artifacts/trade_ideas/closeouts-2026-06.csv
```

Resolution-specific closeout review:

```bash
uv run gpt-trader ideas closeout list \
  --ideas-root var/data/trade_ideas \
  --resolution invalidation \
  --has-evidence true \
  --limit 100 \
  --format json
```

## Output standards

Output standard (this spec is canonical for the `ideas` surface): text mode
prints `✓ ideas <action> OK (<details>)` / `✗ ideas <action> FAILED: <error>`;
exit 0 on success, 1 on failure; JSON mode returns the `CliResponse` envelope
with `command="ideas <subcommand>"`.

## Serialization notes

- `Decimal` fields (`MaxLoss`, `RiskBudget`) serialize as strings via the
  models' own `to_dict`; do not re-serialize with `float()`.
- Timestamps render ISO-8601 UTC.
- Never print secrets; the audit contract forbids credentials in any record.

## Implemented test plan (tests/unit/gpt_trader/cli/commands/)

Use a `tmp_path`-rooted service via `--ideas-root` (no monkeypatching of
globals needed; this is why the flag exists). Fixture helper builds a valid
`TradeIdea` dict matching `eligibility` requirements.

Required cases:

1. `propose` from file and stdin → success, record on disk, audit event
   appended, eligibility warnings surfaced for a non-compliant idea.
2. `propose` with missing required field → `INVALID_ARGUMENT`, names field.
3. `list` empty store → success, empty list. `list --state proposed` filters.
   Advanced list coverage includes non-state filters, sorting, pagination
   metadata, inverted confidence-range validation, and default text-output
   compatibility.
4. `show` unknown id → `IDEA_NOT_FOUND`. `show --events` includes history.
5. `report` empty store, normal records, missing closeout coverage, JSON
   output, schema keys, deterministic report id, date filters, CSV output, and
   read-only behavior that does not create `risk_budget.jsonl`.
6. `replay baseline` text success, malformed fixture input, empty/no-idea
   replay success with `was_noop=True`, JSON output exposing `ReplayReport`
   aggregate fields, precision-preserving JSON-number candle parsing, custom
   moving-average history defaults, semantically invalid OHLCV fixture rows,
   and help text for required read-only flags.
7. `closeout record` for a terminal filled idea with realized amount/percent
   and repeated evidence; `closeout show` returns the persisted attribution.
8. `closeout record` for an expired idea with unavailable P/L reason; proposed
   ideas fail with `VALIDATION_ERROR`; missing realized P/L input fails with
   `MISSING_ARGUMENT`; `closeout show` without attribution succeeds with
   `was_noop=True`.
9. `approve` happy path → state `approved`, human actor in audit event.
10. `approve` over-budget idea → exit 1, `POLICY_VIOLATION`, all violations in
   `data["violations"]` (assert ≥2 violations both present).
11. `request-changes` → `resubmit` (revised record) → `approve` full loop.
12. `reject`, `cancel`, `expire` single, `expire --sweep` with explicit expiry
   coverage (one stale + one fresh idea: only stale expires; `was_noop` when
   none) plus review-latency sweep coverage for a far-future idea whose review
   deadline exceeds `max_review_latency_hours`.
13. `mark-submitted` then `mark-filled` with venue/external id recorded in
   audit events.
14. `budget show` seeds defaults; `budget set --max-loss-per-idea-pct 2
    --reason ...` bumps version; `budget set` with no field flags →
    `MISSING_ARGUMENT`.
15. `audit verify` OK path; tampered line in `audit.jsonl` → failure.
16. JSON mode for at least propose/approve/list/report/replay baseline asserting the
    `CliResponse` envelope
    (`result.errors[0].code == CliErrorCode.POLICY_VIOLATION.value`).
17. `propose-baseline` success from a local snapshot fixture, no-signal
    no-op behavior, duplicate decision handling, malformed snapshot input, and
    JSON output with decision ids, record hashes, states, and approval-preview
    warnings/violations.
18. `export-ticket` happy path for an approved idea, unapproved state guard
    failure, canonical raw JSON output stable for fixed evaluation time, and
    `--out` file behavior.
19. `audit list/export` and `closeout list/export` filters, pagination, JSON
    artifact rows, CSV output, joined closeout terminal-event fields, and
    empty/no-match no-op behavior.

## Acceptance criteria

- [x] `gpt-trader ideas --help` lists all subcommands with accurate help text.
- [x] `gpt-trader ideas replay baseline --help` documents the local fixture
      replay surface and its broker-free boundary.
- [x] Full loop works on a clean checkout with no env vars:
      propose → list → show → approve → mark-submitted → mark-filled,
      and the audit log verifies afterward.
- [x] No import from `features/brokerages/` or `features/live_trade/` in
      `ideas.py` (enforce by review; this surface must stay execution-free).
- [x] All policy violations reach the user; no first-error-only truncation.
- [x] `propose-baseline` turns local candle fixtures into proposed records
      without broker/API access, and reports approval-preview warnings per
      proposal.
- [x] `export-ticket` renders deterministic broker-neutral ticket artifacts
      from approved or terminal-approved ideas without broker/API access.
- [x] The focused `tests/unit/gpt_trader/cli/commands/test_ideas_*.py` suite
      covers the implemented command group.
- [ ] Re-run the repo-required quality bundle before merging any future change
      that modifies this CLI or its tests.
