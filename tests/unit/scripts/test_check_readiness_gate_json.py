from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import scripts.ci.check_readiness_gate as check_readiness_gate
from tests.unit.scripts.test_check_readiness_gate import (
    _setup_green_streak,
    _write_daily_report,
    _write_preflight_report,
)


def test_main_json_require_reports_emits_failure_payload_without_reports(
    tmp_path: Path, capsys
) -> None:
    daily_root = tmp_path / "runtime_data"
    result = check_readiness_gate.main(
        [
            "--profile",
            "canary",
            "--daily-root",
            str(daily_root),
            "--preflight-dir",
            str(tmp_path),
            "--json",
            "--require-reports",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    expected_message = (
        f"Readiness gate skipped: no daily reports found for profile 'canary' under {daily_root}."
    )

    assert result == 1
    assert payload["status"] == "FAILED"
    assert payload["status_message"] == expected_message
    assert payload["failure_reasons"] == [expected_message]
    assert payload["days"] == []
    assert payload["streak_window"]["dates"] == []
    assert payload["reason_codes"] == ["readiness_no_daily_reports"]


def test_main_json_output_passes_with_machine_payload(tmp_path: Path, capsys) -> None:
    profile = "canary"
    _setup_green_streak(tmp_path, profile, date(2026, 1, 17))

    result = check_readiness_gate.main(
        [
            "--profile",
            profile,
            "--daily-root",
            str(tmp_path / "runtime_data"),
            "--preflight-dir",
            str(tmp_path),
            "--json",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 0
    assert payload["status"] == "PASSED"
    assert payload["streak_green"] is True
    assert payload["streak_days"] == 3
    assert payload["thresholds"]["stale_marks_max"] == 0
    assert len(payload["days"]) == 3
    assert all(day["green"] for day in payload["days"])
    assert payload["failure_reasons"] == []
    assert payload["streak_window"]["dates"] == [
        "2026-01-15",
        "2026-01-16",
        "2026-01-17",
    ]
    assert all(day["preflight_status"] == "READY" for day in payload["days"])
    assert payload["reason_codes"] == []
    assert all(day["reason_codes"] == [] for day in payload["days"])


def test_main_json_output_reports_failures(tmp_path: Path, capsys) -> None:
    profile = "canary"
    end_date = date(2026, 1, 17)
    for offset in range(2, -1, -1):
        report_date = end_date - timedelta(days=offset)
        _write_daily_report(
            tmp_path,
            report_date,
            profile=profile,
            fixture_name="daily_report_green.json",
        )
        if offset != 1:
            _write_preflight_report(
                tmp_path,
                report_date,
                profile=profile,
                fixture_name="preflight_ready.json",
            )

    result = check_readiness_gate.main(
        [
            "--profile",
            profile,
            "--daily-root",
            str(tmp_path / "runtime_data"),
            "--preflight-dir",
            str(tmp_path),
            "--json",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 1
    assert payload["status"] == "FAILED"
    assert any("missing preflight report" in reason for reason in payload["failure_reasons"])
    assert any(not day["green"] for day in payload["days"])
    assert payload["reason_codes"] == ["readiness_missing_preflight_report"]
    assert any(
        not day["green"] and day["reason_codes"] == ["readiness_missing_preflight_report"]
        for day in payload["days"]
    )


def test_main_json_output_rejects_diagnostic_only_preflight(tmp_path: Path, capsys) -> None:
    profile = "canary"
    end_date = date(2026, 1, 17)
    _setup_green_streak(tmp_path, profile, end_date)
    _write_preflight_report(
        tmp_path,
        end_date,
        profile=profile,
        fixture_name="preflight_ready.json",
        diagnostic_only=True,
    )

    result = check_readiness_gate.main(
        [
            "--profile",
            profile,
            "--daily-root",
            str(tmp_path / "runtime_data"),
            "--preflight-dir",
            str(tmp_path),
            "--json",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 1
    assert payload["status"] == "FAILED"
    assert payload["reason_codes"] == ["readiness_preflight_diagnostic_only"]
    assert payload["days"][-1]["preflight_status"] == "READY"
    assert payload["days"][-1]["green"] is False
    assert payload["days"][-1]["reason_codes"] == ["readiness_preflight_diagnostic_only"]
    assert any(
        "preflight report is diagnostic-only" in reason for reason in payload["failure_reasons"]
    )


def test_main_json_output_skips_when_no_reports(tmp_path: Path, capsys) -> None:
    daily_root = tmp_path / "runtime_data"
    result = check_readiness_gate.main(
        [
            "--profile",
            "canary",
            "--daily-root",
            str(daily_root),
            "--preflight-dir",
            str(tmp_path),
            "--json",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    expected_message = (
        f"Readiness gate skipped: no daily reports found for profile 'canary' under {daily_root}."
    )
    assert result == 0
    assert payload["status"] == "SKIPPED"
    assert payload["failure_reasons"] == [expected_message]
    assert payload["days"] == []
    assert payload["streak_window"]["dates"] == []
    assert payload["reason_codes"] == ["readiness_no_daily_reports"]
