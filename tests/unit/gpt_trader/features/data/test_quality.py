from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from gpt_trader.features.data.quality import DataQualityChecker


def _build_frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=4, freq="D")
    data = pd.DataFrame(
        {
            "open": [100, 101, 102, 103],
            "high": [101, 102, 103, 104],
            "low": [99, 100, 101, 102],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [1000, 1100, 1200, 1300],
        },
        index=index,
    )
    return data


def test_check_quality_records_history() -> None:
    checker = DataQualityChecker()
    frame = _build_frame()
    quality = checker.check_quality(frame)

    assert 0 < quality.completeness <= 1
    assert checker.quality_history[-1] == quality


def test_check_quality_handles_empty_frame() -> None:
    checker = DataQualityChecker()
    empty = pd.DataFrame()
    quality = checker.check_quality(empty)
    assert quality.completeness == 0
    assert not checker.quality_history, "empty frames should not extend history"


def test_validate_ohlcv_detects_issues() -> None:
    checker = DataQualityChecker()
    index = pd.date_range("2024-01-01", periods=3, freq="D")
    frame = pd.DataFrame(
        {
            "open": [100, 100, -5],
            "high": [99, 100, 101],  # First row high < low
            "low": [100, 95, 90],
            "close": [101, 97, 95],
            "volume": [1000, -10, 1200],
        },
        index=index,
    )

    issues = checker.validate_ohlcv(frame)
    assert any("High < Low" in issue for issue in issues)
    assert any("Negative open" in issue for issue in issues)
    assert any("Negative volume" in issue for issue in issues)


def test_quality_trend_returns_average() -> None:
    checker = DataQualityChecker()
    frame = _build_frame()

    # First check with current timestamps
    checker.check_quality(frame)

    # Older data to pull timeliness score down
    old_index = pd.date_range(datetime.now() - timedelta(days=90), periods=4, freq="D")
    stale_frame = frame.copy()
    stale_frame.index = old_index
    checker.check_quality(stale_frame)

    trend = checker.get_quality_trend()
    assert 0.0 <= trend["timeliness"] <= 1.0


class TestQualityResult:
    """Tests for QualityResult inner class."""

    def test_overall_score(self) -> None:
        result = DataQualityChecker.QualityResult(acceptable=True, score=0.9)
        assert result.overall_score() == 0.9

    def test_is_acceptable_true(self) -> None:
        result = DataQualityChecker.QualityResult(acceptable=True, score=0.9)
        assert result.is_acceptable(threshold=0.8) is True

    def test_is_acceptable_false_by_score(self) -> None:
        result = DataQualityChecker.QualityResult(acceptable=True, score=0.7)
        assert result.is_acceptable(threshold=0.8) is False

    def test_is_acceptable_false_by_flag(self) -> None:
        result = DataQualityChecker.QualityResult(acceptable=False, score=0.9)
        assert result.is_acceptable(threshold=0.8) is False

    def test_completeness_property(self) -> None:
        result = DataQualityChecker.QualityResult(acceptable=True, score=0.75)
        assert result.completeness == 0.75


class TestCheckQualityExtended:
    """Extended tests for check_quality method."""

    def test_handles_none_data(self) -> None:
        checker = DataQualityChecker()
        result = checker.check_quality(None)  # type: ignore[arg-type]
        assert result.overall_score() == 0.0
        assert not result.is_acceptable()

    def test_handles_nulls_in_data(self) -> None:
        checker = DataQualityChecker()
        frame = pd.DataFrame({"a": [1, 2, np.nan], "b": [4, 5, 6]})
        result = checker.check_quality(frame)
        assert result.overall_score() == 0.5
        assert not result.is_acceptable()


class TestValidateOhlcvExtended:
    """Extended tests for validate_ohlcv method."""

    def test_handles_none_data(self) -> None:
        checker = DataQualityChecker()
        issues = checker.validate_ohlcv(None)  # type: ignore[arg-type]
        assert issues == ["Empty dataframe"]

    def test_missing_columns(self) -> None:
        checker = DataQualityChecker()
        frame = pd.DataFrame({"open": [100], "close": [101]})
        issues = checker.validate_ohlcv(frame)
        assert any("Missing columns" in issue for issue in issues)

    def test_negative_high(self) -> None:
        checker = DataQualityChecker()
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        frame = pd.DataFrame(
            {
                "open": [100, 100],
                "high": [-5, 101],
                "low": [99, 99],
                "close": [100, 100],
                "volume": [1000, 1000],
            },
            index=index,
        )
        issues = checker.validate_ohlcv(frame)
        assert any("Negative high" in issue for issue in issues)

    def test_negative_low(self) -> None:
        checker = DataQualityChecker()
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        frame = pd.DataFrame(
            {
                "open": [100, 100],
                "high": [105, 105],
                "low": [-5, 99],
                "close": [100, 100],
                "volume": [1000, 1000],
            },
            index=index,
        )
        issues = checker.validate_ohlcv(frame)
        assert any("Negative low" in issue for issue in issues)

    def test_negative_close(self) -> None:
        checker = DataQualityChecker()
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        frame = pd.DataFrame(
            {
                "open": [100, 100],
                "high": [105, 105],
                "low": [95, 95],
                "close": [-5, 100],
                "volume": [1000, 1000],
            },
            index=index,
        )
        issues = checker.validate_ohlcv(frame)
        assert any("Negative close" in issue for issue in issues)

    def test_valid_frame_no_issues(self) -> None:
        checker = DataQualityChecker()
        frame = _build_frame()
        issues = checker.validate_ohlcv(frame)
        assert issues == []


class TestGetQualityTrendExtended:
    """Extended tests for get_quality_trend method."""

    def test_empty_scores(self) -> None:
        checker = DataQualityChecker()
        trend = checker.get_quality_trend()
        assert trend["completeness"] == 0.0
        assert trend["overall"] == 0.0

    def test_multiple_scores(self) -> None:
        checker = DataQualityChecker()
        checker.scores = [1.0, 0.5, 0.5]
        trend = checker.get_quality_trend()
        assert trend["completeness"] == 2.0 / 3.0
