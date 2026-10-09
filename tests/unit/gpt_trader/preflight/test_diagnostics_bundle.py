from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader.preflight.diagnostics_bundle import (
    CHECK_NAMES,
    _format_readiness_payload,
    build_diagnostics_bundle,
)


def _make_result(
    status: str, message: str, details: dict[str, object] | None = None
) -> dict[str, object]:
    return {"status": status, "message": message, "details": details or {}}


def test_readiness_summary_empty() -> None:
    payload = _format_readiness_payload([])

    assert payload["status"] == "UNKNOWN"
    assert payload["diagnostic_only"] is False
    assert payload["counts"]["total"] == 0
    assert payload["checks"] == []


def test_readiness_summary_partial() -> None:
    payload = _format_readiness_payload(
        [
            _make_result("warn", "slow response"),
            _make_result("fail", "api down", {"errors": ["timeout"]}),
        ]
    )

    assert payload["status"] == "NOT READY"
    assert payload["diagnostic_only"] is False
    assert payload["counts"]["warn"] == 1
    assert payload["counts"]["fail"] == 1
    assert payload["counts"]["total"] == 2
    assert payload["checks"]


def test_readiness_summary_all_passes() -> None:
    payload = _format_readiness_payload(
        [_make_result("pass", "ok"), _make_result("pass", "still ok")]
    )

    assert payload["status"] == "READY"
    assert payload["counts"]["pass"] == 2
    assert payload["counts"]["total"] == 2
    assert payload["message"].startswith("Preflight evidence is READY")
    assert "production trading" in payload["message"]
    assert "System is READY for production trading" not in payload["message"]


def test_readiness_summary_warn_only_is_diagnostic_only() -> None:
    payload = _format_readiness_payload(
        [_make_result("pass", "ok")],
        diagnostic_only=True,
    )

    assert payload["status"] == "READY"
    assert payload["diagnostic_only"] is True
    assert payload["message"].startswith("DIAGNOSTIC-ONLY:")
    assert "do not satisfy the live readiness gate" in payload["message"]


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "diagnostics_bundle"
PROFILE_NAME = "golden-profile"
FIXED_TIMESTAMP = datetime(2025, 2, 5, 12, 34, 56, tzinfo=UTC)
FIXED_PYTHON_VERSION = "3.12.0"
FIXED_PLATFORM = "TestOS 1.0"
FIXED_CWD_PATH = Path("/tmp/diagnostics")
ENV_DEFAULTS: dict[str, tuple[str, bool]] = {
    "API_KEY": ("super-secret-key", True),
    "REGION": ("us-east-1", False),
}
TRADING_MODES = ["spot", "cfm"]


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class _DummyContext:
    def __init__(
        self,
        *,
        profile: str,
        results: list[dict[str, Any]],
        credentials: object | None = None,
    ) -> None:
        self.profile = profile
        self.results = list(results)
        self._credentials = credentials

    def expected_env_defaults(self) -> dict[str, tuple[str, bool]]:
        return dict(ENV_DEFAULTS)

    def trading_modes(self) -> list[str]:
        return list(sorted(TRADING_MODES))

    def cfm_enabled(self) -> bool:
        return True

    def intends_real_orders(self) -> bool:
        return True

    def requires_trade_permission(self) -> bool:
        return True

    def resolve_cdp_credentials_info(self) -> object | None:
        return self._credentials

    def should_skip_remote_checks(self) -> bool:
        return False


def _make_preflight_stub(context: _DummyContext) -> type:
    class StubPreflightCheck:
        def __init__(self, *, verbose: bool = False, profile: str = PROFILE_NAME) -> None:
            self.verbose = verbose
            self.profile = profile
            self.context = context

        def check_environment_variables(self) -> None:  # pragma: no cover - stub
            return None

        def check_pretrade_diagnostics(self) -> None:  # pragma: no cover - stub
            return None

        def check_readiness_report(self) -> None:  # pragma: no cover - stub
            return None

    return StubPreflightCheck


def _patch_stable_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDatetimeValue(datetime):
        def __new__(cls, *args: object, **kwargs: object) -> FixedDatetimeValue:
            return datetime.__new__(cls, *args, **kwargs)

        def astimezone(self, tz=None) -> FixedDatetimeValue:
            return self

        def tzname(self) -> str:
            return "UTC"

    class FixedDatetime:
        @classmethod
        def now(cls, tz=None) -> FixedDatetimeValue:
            return FixedDatetimeValue(
                FIXED_TIMESTAMP.year,
                FIXED_TIMESTAMP.month,
                FIXED_TIMESTAMP.day,
                FIXED_TIMESTAMP.hour,
                FIXED_TIMESTAMP.minute,
                FIXED_TIMESTAMP.second,
                tzinfo=UTC,
            )

    monkeypatch.setattr(
        "gpt_trader.preflight.diagnostics_bundle.datetime",
        FixedDatetime,
    )
    monkeypatch.setattr(
        "gpt_trader.preflight.diagnostics_bundle.platform.python_version",
        lambda: FIXED_PYTHON_VERSION,
    )
    monkeypatch.setattr(
        "gpt_trader.preflight.diagnostics_bundle.platform.platform",
        lambda: FIXED_PLATFORM,
    )
    monkeypatch.setattr(
        "gpt_trader.preflight.diagnostics_bundle.Path.cwd",
        classmethod(lambda cls: FIXED_CWD_PATH),
    )


def _build_bundle(
    monkeypatch: pytest.MonkeyPatch,
    results: list[dict[str, Any]],
    *,
    credentials: object | None = None,
    warn_only: bool = False,
) -> dict[str, Any]:
    context = _DummyContext(profile=PROFILE_NAME, results=results, credentials=credentials)
    monkeypatch.setattr(
        "gpt_trader.preflight.diagnostics_bundle.PreflightCheck",
        _make_preflight_stub(context),
    )
    _patch_stable_environment(monkeypatch)
    return build_diagnostics_bundle(PROFILE_NAME, warn_only=warn_only)


def test_diagnostics_bundle_matches_fixture_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        _make_result("pass", "env ready", {"api_key": "abc", "region": "us-east-1"}),
        _make_result(
            "pass",
            "diagnostics stable",
            {"nested": {"api_key": "abc"}, "status_code": 200},
        ),
    ]

    bundle = _build_bundle(monkeypatch, results)
    expected = _load_fixture("healthy.json")

    assert bundle == expected
    assert "System is READY for production trading" not in bundle["bundle"]["readiness"]["message"]
    assert list(bundle["bundle"].keys()) == ["readiness", "config", "environment"]
    assert list(bundle["bundle"]["config"].keys()) == [
        "profile",
        "warn_only",
        "trading_modes",
        "cfm_enabled",
        "intx_perps_enabled",
        "intends_real_orders",
        "requires_trade_permission",
        "expected_env_defaults",
        "checks_run",
    ]
    assert bundle["bundle"]["config"]["expected_env_defaults"]["API_KEY"]["value"] == "<redacted>"
    assert bundle["bundle"]["config"]["checks_run"] == list(CHECK_NAMES)


def test_diagnostics_bundle_matches_fixture_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        _make_result("pass", "env ready", {"status_code": 200}),
        _make_result(
            "warn",
            "slow response",
            {"credentials": {"api_key": "secret"}, "timeout": "5s"},
        ),
        _make_result("pass", "market ok", {"notes": "on watch", "secret_token": "token"}),
    ]

    bundle = _build_bundle(monkeypatch, results)
    expected = _load_fixture("partial.json")

    assert bundle == expected
    checks = bundle["bundle"]["readiness"]["checks"]
    assert checks[1]["details"]["credentials"] == "<redacted>"
    assert checks[2]["details"]["secret_token"] == "<redacted>"


def test_diagnostics_bundle_matches_fixture_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        _make_result(
            "fail",
            "api down",
            {
                "api_key": "abc",
                "errors": [{"code": "rate_limit", "private_info": "secret"}],
            },
        ),
        _make_result("pass", "fallback ready", {"notes": "retrying"}),
        _make_result("pass", "final check", {"info": "done"}),
    ]

    bundle = _build_bundle(monkeypatch, results, credentials=object())
    expected = _load_fixture("degraded.json")

    assert bundle == expected
    assert (
        bundle["bundle"]["readiness"]["checks"][0]["details"]["errors"][0]["private_info"]
        == "<redacted>"
    )
    assert bundle["bundle"]["environment"]["cdp_credentials_present"] is True
    assert bundle["bundle"]["readiness"]["status"] == "NOT READY"


def test_diagnostics_bundle_warn_only_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [_make_result("pass", "env ready", {"status_code": 200})]
    monkeypatch.setenv("GPT_TRADER_PREFLIGHT_WARN_ONLY", "1")

    bundle = _build_bundle(monkeypatch, results)

    readiness = bundle["bundle"]["readiness"]
    assert readiness["diagnostic_only"] is True
    assert readiness["message"].startswith("DIAGNOSTIC-ONLY:")
    assert "do not satisfy the live readiness gate" in readiness["message"]


def test_diagnostics_bundle_warn_only_scopes_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [_make_result("pass", "env ready", {"status_code": 200})]
    context = _DummyContext(profile=PROFILE_NAME, results=results)
    observed_warn_only_values: list[str | None] = []

    class StubPreflightCheck:
        def __init__(self, *, verbose: bool = False, profile: str = PROFILE_NAME) -> None:
            self.verbose = verbose
            self.profile = profile
            self.context = context

        def check_environment_variables(self) -> None:
            observed_warn_only_values.append(os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY"))

        def check_pretrade_diagnostics(self) -> None:
            observed_warn_only_values.append(os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY"))

        def check_readiness_report(self) -> None:
            observed_warn_only_values.append(os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY"))

    monkeypatch.delenv("GPT_TRADER_PREFLIGHT_WARN_ONLY", raising=False)
    monkeypatch.setattr(
        "gpt_trader.preflight.diagnostics_bundle.PreflightCheck",
        StubPreflightCheck,
    )
    _patch_stable_environment(monkeypatch)

    bundle = build_diagnostics_bundle(PROFILE_NAME, warn_only=True)

    assert observed_warn_only_values == ["1", "1", "1"]
    assert os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY") is None
    assert bundle["bundle"]["readiness"]["diagnostic_only"] is True
    assert bundle["bundle"]["config"]["warn_only"] is True
