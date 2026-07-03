"""Tests for optimize apply output builder."""

from typing import Any

from gpt_trader.cli.commands.optimize import apply


def _sample_run_data() -> dict[str, Any]:
    """Return run data with mixed strategy, risk, and simulation params."""
    return {
        "run_id": "opt_legacy",
        "study_name": "legacy-study",
        "best_parameters": {
            "short_ma_period": 10,
            "long_ma_period": 40,
            "custom_signal_weight": 0.35,
            "target_leverage": 4,
            "max_leverage": 7,
            "daily_loss_limit_pct": 0.04,
            "fee_tier": "premium",
            "spread_impact_pct": 0.015,
        },
    }


def test_build_output_config_distributes_risk_parameters():
    """Risk fields should land under risk while strategy fields stay in strategy."""
    run_data = _sample_run_data()
    output = apply._build_output_config(run_data, {}, "optimized", strategy_only=False)

    # Strategy params should not include risk or simulation fields
    assert output["strategy"]["short_ma_period"] == 10
    assert output["strategy"]["custom_signal_weight"] == 0.35
    assert "target_leverage" not in output["strategy"]

    # Risk params should include fields from the risk parameter space
    assert output["risk"]["target_leverage"] == 4
    assert output["risk"]["max_leverage"] == 7
    # Removed parameter (#1120 stage 3): dropped instead of re-applied.
    assert "daily_loss_limit_pct" not in output["risk"]

    # Simulation params should still be captured
    assert output["simulation"]["fee_tier"] == "premium"
    assert output["simulation"]["spread_impact_pct"] == 0.015


def test_build_output_config_strategy_only_omits_risk_and_simulation():
    """Strategy-only flag should prevent risk/simulation sections from being added."""
    run_data = _sample_run_data()
    output = apply._build_output_config(run_data, {}, "optimized", strategy_only=True)

    assert "risk" not in output
    assert "simulation" not in output
    assert output["strategy"]["short_ma_period"] == 10
    assert output["strategy"]["custom_signal_weight"] == 0.35
