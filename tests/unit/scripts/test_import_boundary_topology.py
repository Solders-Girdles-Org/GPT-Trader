"""Frozen import-topology pins for the boundary guard.

Behavioral tests for the guard's rule machinery live in
``test_check_import_boundaries.py``; this module only pins the ratcheted
allowlists so an edge cannot appear or vanish without a reviewed diff here.
"""

from __future__ import annotations

import scripts.ci.check_import_boundaries as check_import_boundaries


def test_cross_slice_allowlist_is_frozen_topology() -> None:
    # The ratchet encodes today's verified slice-to-slice edges. Adding an edge
    # here requires an architecture rationale (docs/ARCHITECTURE.md); removing
    # one is progress and should just update this test.
    assert check_import_boundaries.CROSS_SLICE_ALLOWED_EDGES == frozenset(
        {
            # Paper execution lane consumes APPROVED ideas and drives paper/mock
            # brokers only (docs/decisions/adopt-five-role-composition.md).
            ("idea_execution", "trade_ideas"),
            ("idea_execution", "brokerages"),
            ("intelligence", "live_trade"),
            ("live_trade", "brokerages"),
            ("live_trade", "intelligence"),
            # Composition root constructs recorder-owned tick state; the engine
            # consumes it injected (docs/decisions/adopt-five-role-composition.md).
            ("live_trade", "recorder"),
            ("live_trade", "strategy_tools"),
            ("live_trade", "trade_ideas"),
            # Recorder owns Coinbase market-data acquisition and produces the
            # MarketSnapshot artifact defined by the trade-idea contract
            # (docs/decisions/adopt-five-role-composition.md).
            ("recorder", "brokerages"),
            ("recorder", "trade_ideas"),
            # RegimeAwareProposer overlays regime state; PositionSizer bridge
            # enriches sizing on trade-idea proposal records.
            ("trade_ideas", "intelligence"),
            ("optimize", "live_trade"),
            ("strategy_tools", "trade_ideas"),
        }
    )


def test_cross_slice_narrow_import_prefixes_are_frozen() -> None:
    assert check_import_boundaries.CROSS_SLICE_NARROW_IMPORT_PREFIXES == frozenset(
        {
            (
                "idea_execution",
                "brokerages",
                "gpt_trader.features.brokerages.mock",
            ),
            (
                "idea_execution",
                "brokerages",
                "gpt_trader.features.brokerages.paper",
            ),
            (
                "recorder",
                "brokerages",
                "gpt_trader.features.brokerages.coinbase",
            ),
        }
    )


def test_cross_slice_narrow_import_prefixes_have_topology_edges() -> None:
    for source, target, _prefix in check_import_boundaries.CROSS_SLICE_NARROW_IMPORT_PREFIXES:
        assert (source, target) in check_import_boundaries.CROSS_SLICE_ALLOWED_EDGES


def test_trade_ideas_allowed_prefixes_are_frozen() -> None:
    assert check_import_boundaries.TRADE_IDEAS_ALLOWED_IMPORT_PREFIXES == (
        "gpt_trader.core",
        "gpt_trader.errors",
        # Regime-aware proposer reads MarketRegimeDetector; PositionSizer
        # bridge adds deterministic sizing metadata pre-approval.
        "gpt_trader.features.intelligence.regime",
        "gpt_trader.features.intelligence.sizing",
        "gpt_trader.features.trade_ideas",
    )
