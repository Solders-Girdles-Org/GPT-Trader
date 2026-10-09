"""Tests for report summaries and CSV rows."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from tests.unit.gpt_trader.backtesting.metrics.report_test_utils import (  # naming: allow
    create_mock_broker,
    create_mock_risk_metrics,
    create_mock_trade_stats,
)

import gpt_trader.backtesting.metrics.report as report_module
from gpt_trader.backtesting.metrics.report import BacktestReporter
from gpt_trader.backtesting.metrics.risk import RiskMetrics


@pytest.fixture
def reporter() -> BacktestReporter:
    return BacktestReporter(create_mock_broker())


@pytest.fixture(autouse=True)
def report_stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    trade_stats = create_mock_trade_stats()
    risk_metrics = create_mock_risk_metrics()

    calculate_trade_statistics = MagicMock(return_value=trade_stats)
    calculate_risk_metrics = MagicMock(return_value=risk_metrics)

    monkeypatch.setattr(report_module, "calculate_trade_statistics", calculate_trade_statistics)
    monkeypatch.setattr(report_module, "calculate_risk_metrics", calculate_risk_metrics)

    return {
        "calculate_trade_statistics": calculate_trade_statistics,
        "calculate_risk_metrics": calculate_risk_metrics,
    }


class TestBacktestReporterGenerateCsvRow:
    """Tests for generate_csv_row method."""

    def test_generate_csv_row_returns_dict(self, reporter: BacktestReporter) -> None:
        row = reporter.generate_csv_row()

        assert isinstance(row, dict)

    def test_generate_csv_row_includes_all_keys(self, reporter: BacktestReporter) -> None:
        expected_keys = [
            "initial_equity",
            "final_equity",
            "total_return_pct",
            "total_return_usd",
            "max_drawdown_pct",
            "max_drawdown_usd",
            "sharpe_ratio",
            "sortino_ratio",
            "total_trades",
            "winning_trades",
            "losing_trades",
            "win_rate_pct",
            "profit_factor",
            "total_fees",
            "avg_slippage_bps",
            "funding_pnl",
        ]

        row = reporter.generate_csv_row()

        for key in expected_keys:
            assert key in row, f"Missing key: {key}"

    def test_generate_csv_row_values_are_numeric(self, reporter: BacktestReporter) -> None:
        row = reporter.generate_csv_row()

        for key, value in row.items():
            assert isinstance(value, (int, float)), (
                f"Key {key} has non-numeric value: {type(value)}"
            )

    def test_generate_csv_row_handles_none_sharpe(
        self,
        report_stubs: dict[str, MagicMock],
        reporter: BacktestReporter,
    ) -> None:
        report_stubs["calculate_risk_metrics"].return_value = RiskMetrics(
            max_drawdown_pct=Decimal("15"),
            max_drawdown_usd=Decimal("15000"),
            avg_drawdown_pct=Decimal("5"),
            drawdown_duration_days=5,
            total_return_pct=Decimal("0"),
            annualized_return_pct=Decimal("0"),
            daily_return_avg=Decimal("0"),
            daily_return_std=Decimal("0"),
            sharpe_ratio=None,
            sortino_ratio=None,
            calmar_ratio=None,
            volatility_annualized=Decimal("0"),
            downside_volatility=Decimal("0"),
            max_leverage_used=Decimal("0"),
            avg_leverage_used=Decimal("0"),
            time_in_market_pct=Decimal("0"),
            var_95_daily=Decimal("0"),
            var_99_daily=Decimal("0"),
        )

        row = reporter.generate_csv_row()

        assert row["sharpe_ratio"] == 0.0
        assert row["sortino_ratio"] == 0.0


class TestBacktestReporterGenerateSummary:
    """Tests for generate_summary method."""

    def test_generate_summary_returns_string(self, reporter: BacktestReporter) -> None:
        summary = reporter.generate_summary()

        assert isinstance(summary, str)
        assert summary

    def test_generate_summary_includes_performance_section(
        self, reporter: BacktestReporter
    ) -> None:
        summary = reporter.generate_summary()

        assert "PERFORMANCE" in summary
        assert "Initial Equity" in summary
        assert "Final Equity" in summary
        assert "Total Return" in summary

    def test_generate_summary_includes_risk_metrics_section(
        self, reporter: BacktestReporter
    ) -> None:
        summary = reporter.generate_summary()

        assert "RISK METRICS" in summary
        assert "Max Drawdown" in summary
        assert "Sharpe Ratio" in summary
        assert "Sortino Ratio" in summary

    def test_generate_summary_includes_trade_statistics_section(
        self, reporter: BacktestReporter
    ) -> None:
        summary = reporter.generate_summary()

        assert "TRADE STATISTICS" in summary
        assert "Total Trades" in summary
        assert "Win Rate" in summary
        assert "Profit Factor" in summary

    def test_generate_summary_includes_costs_section(self, reporter: BacktestReporter) -> None:
        summary = reporter.generate_summary()

        assert "COSTS" in summary
        assert "Total Fees" in summary
        assert "Avg Slippage" in summary
        assert "Funding PnL" in summary

    def test_generate_summary_handles_none_sharpe(
        self,
        report_stubs: dict[str, MagicMock],
        reporter: BacktestReporter,
    ) -> None:
        report_stubs["calculate_risk_metrics"].return_value = RiskMetrics(
            max_drawdown_pct=Decimal("15"),
            max_drawdown_usd=Decimal("15000"),
            avg_drawdown_pct=Decimal("5"),
            drawdown_duration_days=5,
            total_return_pct=Decimal("0"),
            annualized_return_pct=Decimal("0"),
            daily_return_avg=Decimal("0"),
            daily_return_std=Decimal("0"),
            sharpe_ratio=None,
            sortino_ratio=None,
            calmar_ratio=None,
            volatility_annualized=Decimal("0"),
            downside_volatility=Decimal("0"),
            max_leverage_used=Decimal("0"),
            avg_leverage_used=Decimal("0"),
            time_in_market_pct=Decimal("0"),
            var_95_daily=Decimal("0"),
            var_99_daily=Decimal("0"),
        )

        summary = reporter.generate_summary()

        assert "Sharpe Ratio:       N/A" in summary
        assert "Sortino Ratio:      N/A" in summary
