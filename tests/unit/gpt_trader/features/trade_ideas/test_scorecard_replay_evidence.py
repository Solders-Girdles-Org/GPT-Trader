"""Replay-derived evidence labeling for the promotion scorecard.

Split from test_scorecard.py (400-line hygiene cap): the pure
``build_replay_evidence`` contract — labeling, benchmark edge vs the
deterministic baseline, and the capital-weighted sizing channel (#1244) —
independent of any stored idea trail.
"""

from __future__ import annotations

from gpt_trader.features.trade_ideas.scorecard import (
    REPLAY_EVIDENCE_LABEL,
    _replay_evidence_lines,
    build_replay_evidence,
)


def test_replay_evidence_is_labeled_and_reports_edge_vs_baseline() -> None:
    tournament_payload = {
        "symbol": "BTC-USD",
        "granularity": "ONE_HOUR",
        "source": "fixture:candles",
        "snapshots_evaluated": 240,
        "rankings": [],
        "reports": [
            {
                "proposer_id": "baseline-ma-10-50",
                "ideas_proposed": 12,
                "resolved_ideas": 10,
                "target_hit_rate": "0.4",
                "stop_hit_rate": "0.6",
                "average_return_r": "0.05",
                "eligibility_pass_rate": "1",
            },
            {
                "proposer_id": "regime-switcher",
                "ideas_proposed": 9,
                "resolved_ideas": 8,
                "target_hit_rate": "0.5",
                "stop_hit_rate": "0.5",
                "average_return_r": "0.35",
                "eligibility_pass_rate": "1",
            },
        ],
    }

    evidence = build_replay_evidence(tournament_payload)

    assert evidence["evidence"] == REPLAY_EVIDENCE_LABEL
    assert evidence["snapshots_evaluated"] == 240
    assert [row["proposer_id"] for row in evidence["calibration"]] == [
        "baseline-ma-10-50",
        "regime-switcher",
    ]
    edge = evidence["benchmark_edge"]
    assert edge["baseline_proposer_id"] == "baseline-ma-10-50"
    assert edge["comparisons"] == [
        {
            "proposer_id": "regime-switcher",
            "average_return_r": "0.35",
            "edge_r": "0.3000",
        }
    ]


def test_replay_evidence_from_single_baseline_report_has_no_edge() -> None:
    single_payload = {
        "proposer_id": "baseline-ma-10-50",
        "symbol": "BTC-USD",
        "granularity": "ONE_HOUR",
        "source": "fixture:candles",
        "snapshots_evaluated": 100,
        "ideas_proposed": 5,
        "resolved_ideas": 4,
        "target_hit_rate": "0.5",
        "stop_hit_rate": "0.5",
        "average_return_r": "0.2",
        "eligibility_pass_rate": "1",
    }

    evidence = build_replay_evidence(single_payload)

    assert evidence["evidence"] == REPLAY_EVIDENCE_LABEL
    assert evidence["benchmark_edge"]["comparisons"] == []
    assert "non-baseline" in evidence["benchmark_edge"]["detail"]


def test_replay_evidence_carries_capital_weighted_metrics_when_present() -> None:
    payload = {
        "symbol": "BTC-USD",
        "granularity": "ONE_HOUR",
        "source": "fixture:candles",
        "snapshots_evaluated": 240,
        "rankings": [],
        "reports": [
            {
                "proposer_id": "baseline-ma-10-50",
                "ideas_proposed": 12,
                "resolved_ideas": 10,
                "target_hit_rate": "0.4",
                "stop_hit_rate": "0.6",
                "average_return_r": "0.05",
                "capital_weighted_average_return_r": "0.02",
                "capital_weighted_sample": 10,
                "eligibility_pass_rate": "1",
            },
            {
                "proposer_id": "conviction-sizer",
                "ideas_proposed": 12,
                "resolved_ideas": 10,
                "target_hit_rate": "0.4",
                "stop_hit_rate": "0.6",
                "average_return_r": "0.05",
                "capital_weighted_average_return_r": "0.80",
                "capital_weighted_sample": 10,
                "eligibility_pass_rate": "1",
            },
        ],
    }

    evidence = build_replay_evidence(payload)

    weighted = {
        row["proposer_id"]: row["capital_weighted_average_return_r"]
        for row in evidence["calibration"]
    }
    # Identical per-idea averages, sizing-visible difference preserved.
    assert weighted == {"baseline-ma-10-50": "0.02", "conviction-sizer": "0.80"}
    rendered = "\n".join(_replay_evidence_lines(evidence))
    assert "capital_weighted_avg_r=0.80 (n=10)" in rendered


def test_replay_evidence_renders_pre_weighted_artifacts_without_the_weighted_clause() -> None:
    # Replay artifacts written before #1244 carry no capital-weighted keys;
    # they must render with the per-idea metrics alone, not crash or print
    # a None clause.
    payload = {
        "proposer_id": "baseline-ma-10-50",
        "symbol": "BTC-USD",
        "granularity": "ONE_HOUR",
        "source": "fixture:candles",
        "snapshots_evaluated": 100,
        "ideas_proposed": 5,
        "resolved_ideas": 4,
        "target_hit_rate": "0.5",
        "stop_hit_rate": "0.5",
        "average_return_r": "0.2",
        "eligibility_pass_rate": "1",
    }

    evidence = build_replay_evidence(payload)

    assert evidence["calibration"][0]["capital_weighted_average_return_r"] is None
    rendered = "\n".join(_replay_evidence_lines(evidence))
    assert "capital_weighted" not in rendered
    assert "avg_r=0.2" in rendered


def test_replay_evidence_renders_proposer_counterfactuals_when_present() -> None:
    payload = {
        "symbol": "BTC-USD",
        "granularity": "ONE_HOUR",
        "source": "fixture:candles",
        "snapshots_evaluated": 240,
        "rankings": [],
        "reports": [
            {
                "proposer_id": "baseline-ma-10-50",
                "ideas_proposed": 12,
                "resolved_ideas": 10,
                "target_hit_rate": "0.4",
                "stop_hit_rate": "0.6",
                "average_return_r": "0.05",
                "eligibility_pass_rate": "1",
            },
            {
                "proposer_id": "regime-aware-ma-10-50",
                "ideas_proposed": 8,
                "resolved_ideas": 8,
                "target_hit_rate": "0.5",
                "stop_hit_rate": "0.5",
                "average_return_r": "0.15",
                "eligibility_pass_rate": "1",
                "proposer_diagnostics": {
                    "candidate_ideas": 12,
                    "emitted_ideas": 8,
                    "unknown_skipped": 1,
                    "suppressed_by_regime": {"BEAR_VOLATILE": 2, "CRISIS": 1},
                    "exit_plans_adjusted": 4,
                    "emitted_by_regime": {"BULL_QUIET": 5, "BULL_VOLATILE": 3},
                },
            },
        ],
    }

    evidence = build_replay_evidence(payload)

    rows = {row["proposer_id"]: row for row in evidence["calibration"]}
    assert rows["baseline-ma-10-50"]["proposer_diagnostics"] is None
    assert rows["regime-aware-ma-10-50"]["proposer_diagnostics"]["emitted_ideas"] == 8
    rendered = "\n".join(_replay_evidence_lines(evidence))
    assert (
        "regime-aware-ma-10-50 counterfactuals: candidates=12, emitted=8, "
        "unknown_skipped=1, suppressed={BEAR_VOLATILE:2, CRISIS:1}, "
        "exit_plans_adjusted=4, emitted_by_regime={BULL_QUIET:5, BULL_VOLATILE:3}"
    ) in rendered
    # No counterfactual line for proposers without diagnostics.
    assert "baseline-ma-10-50 counterfactuals" not in rendered
