# Project Status — Source Pointers

---
status: current
---

This page points to implemented capabilities and their evidence.
[Information Architecture](INFORMATION_ARCHITECTURE.md) gives code, tests,
generated inventories and live GitHub state precedence over status prose.
If a pointer or claim disagrees with those sources, reconcile this page; it
cannot override observed behavior or an accepted decision.

[Direction](DIRECTION.md) owns the destination and execution gates,
[decisions](decisions/README.md) own durable choices, and the
[issue tracker](https://github.com/Solders-Girdles-Org/GPT-Trader/issues) owns
accepted next work. Source integration is separate from runtime deployment,
migration, authorization and measured promotion.

## Source snapshot (2026-09-04)

Pointers checked against `main` through the merged transactional state,
strategy routing and paper cycle changes
([#1277](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1277),
[#1275](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1275),
[#1278](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1278)). No account,
operational state, runtime migration or graduation result was inspected for
this source snapshot.

| Implemented surface | Source and evidence |
| --- | --- |
| Trade-idea records, approval workflow, policy, budgets, audit and attribution | [Service](../src/gpt_trader/features/trade_ideas/service.py) and [feature tests](../tests/unit/gpt_trader/features/trade_ideas/) |
| Transactional admission and persistent trade state | [Persistence](../src/gpt_trader/features/trade_ideas/persistence.py), [transaction tests](../tests/unit/gpt_trader/features/trade_ideas/test_state_transactions.py), and accepted [storage/migration contract](decisions/transactional-trade-state.md) |
| Strategy-to-idea routing and in-process paper continuation | [Routing contract](specs/TRADE_IDEA_INTERFACES_DESIGN_NOTES.md#live-strategy-signal-routing-default-off), [routing tests](../tests/unit/gpt_trader/features/live_trade/engines/test_strategy_routing_contract.py), and [event lane](../src/gpt_trader/features/idea_execution/event_lane.py) |
| Paper-cycle failure isolation and venue price increments | [Paper execution contract](paper_trading.md#product-increments-and-partial-cycle-results), [cycle tests](../tests/unit/gpt_trader/features/idea_execution/test_cycle_failure_isolation.py), and [snapshot increment tests](../tests/unit/gpt_trader/features/recorder/test_snapshot_increments.py) |
| Portfolio monitors, approval policy and measured promotion scoring | [Monitors](../src/gpt_trader/features/trade_ideas/monitors.py), [policy](../src/gpt_trader/features/trade_ideas/policy.py), [scorecard](../src/gpt_trader/features/trade_ideas/scorecard.py), and [measured-outcome decision](decisions/adopt-measured-outcome-rubric.md) |

## Reading source and operational state separately

The [transactional-state decision](decisions/transactional-trade-state.md#adoption-and-rollback)
owns migration, writer coordination and rollback. Committed SQLite support does
not prove that an existing runtime store has been migrated.

The [routing contract](specs/TRADE_IDEA_INTERFACES_DESIGN_NOTES.md#live-strategy-signal-routing-default-off)
owns gate precedence and submission boundaries. Tracked
[paper configuration](../config/profiles/paper.yaml) is an implementation input;
a documentation review does not establish the effective mode of a running bot.

The [staged ladder](DIRECTION.md#the-ladder) and
[graduation contract](DIRECTION.md#graduation) require operational evidence and
recorded decisions. Existing mechanisms and green source tests do not by
themselves prove entry into bounded autonomy or approval for live orders.

## Keeping this page current

Update a source pointer when its capability changes, preferably in the same PR.
Keep detailed behavior in its owning contract, volatile work in GitHub issues
and PRs, and runtime evidence in its prescribed local stores. Preserve accepted
direction while correcting stale status text; an observed implementation does
not silently amend a policy or grant authority.
