from __future__ import annotations

import json
from typing import Any

import pytest
from scripts.agents.pr_readiness import ReadinessReport, apply_protection_drift
from scripts.ci import check_branch_protection as checker


def _matching_payload() -> dict[str, Any]:
    return {
        "required_status_checks": {
            "strict": False,
            "contexts": sorted(checker.EXPECTED_REQUIRED_CHECKS),
        },
        "enforce_admins": {"enabled": True},
        "required_conversation_resolution": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }


def test_matching_payload_has_no_drift() -> None:
    assert checker.assess_protection_drift(_matching_payload()) == []


def test_matching_payload_modern_checks_shape() -> None:
    payload = _matching_payload()
    payload["required_status_checks"] = {
        "strict": False,
        "checks": [{"context": name} for name in checker.EXPECTED_REQUIRED_CHECKS],
    }
    assert checker.assess_protection_drift(payload) == []


def test_missing_payload_is_drift() -> None:
    assert checker.assess_protection_drift(None) == [
        "branch protection payload is missing or empty"
    ]
    assert checker.assess_protection_drift({}) == ["branch protection payload is missing or empty"]


def test_missing_and_extra_required_checks_are_drift() -> None:
    payload = _matching_payload()
    contexts = set(payload["required_status_checks"]["contexts"])
    contexts.discard("Unit Tests (Core)")
    contexts.add("Nightly Extras")
    payload["required_status_checks"]["contexts"] = sorted(contexts)

    drift = checker.assess_protection_drift(payload)

    assert any("missing: Unit Tests (Core)" in item for item in drift)
    assert any("unexpected required checks: Nightly Extras" in item for item in drift)


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        (lambda p: p["required_status_checks"].update(strict=True), "strict up-to-date is True"),
        (lambda p: p.update(enforce_admins={"enabled": False}), "enforce_admins is False"),
        (
            lambda p: p.update(required_conversation_resolution={"enabled": False}),
            "required conversation resolution is False",
        ),
        (
            lambda p: p.update(
                required_pull_request_reviews={"required_approving_review_count": 1}
            ),
            "required approving reviews is 1",
        ),
    ],
)
def test_each_contract_field_drifts_independently(mutation, expected_fragment: str) -> None:
    payload = _matching_payload()
    mutation(payload)

    drift = checker.assess_protection_drift(payload)

    assert len(drift) == 1
    assert expected_fragment in drift[0]


def test_main_reports_success(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(checker, "fetch_protection", lambda repo, branch: _matching_payload())

    exit_code = checker.main([])

    assert exit_code == 0
    assert "✓ Branch protection matches the expected contract" in capsys.readouterr().out


def test_main_reports_drift_and_fails(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    payload = _matching_payload()
    payload["required_status_checks"]["strict"] = True
    monkeypatch.setattr(checker, "fetch_protection", lambda repo, branch: payload)

    exit_code = checker.main([])

    assert exit_code == 1
    assert "✗ Branch protection drift: strict up-to-date is True" in capsys.readouterr().out


def test_main_reports_fetch_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def _raise(repo: str, branch: str) -> dict[str, Any]:
        raise RuntimeError("gh: Not Found (HTTP 404)")

    monkeypatch.setattr(checker, "fetch_protection", _raise)

    exit_code = checker.main([])

    assert exit_code == 1
    assert "✗ Branch protection check FAILED" in capsys.readouterr().out


def test_fetch_protection_rejects_non_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        returncode = 0
        stdout = json.dumps(["not", "a", "mapping"])
        stderr = ""

    monkeypatch.setattr(checker.subprocess, "run", lambda *args, **kwargs: _Result())

    with pytest.raises(RuntimeError, match="unexpected branch protection payload shape"):
        checker.fetch_protection("owner/repo", "main")


# --------------------------------------------------------------------------- #
# agent-pr-ready wiring (apply_protection_drift)
# --------------------------------------------------------------------------- #
def test_apply_protection_drift_adds_warnings_on_main() -> None:
    report = ReadinessReport(ready=True)
    payload = _matching_payload()
    payload["required_status_checks"]["strict"] = True

    apply_protection_drift(report, payload, "main")

    assert report.ready is True  # advisory only
    warnings = [finding for finding in report.findings if finding.severity == "warning"]
    assert any("Branch protection drift: strict up-to-date" in f.message for f in warnings)


def test_apply_protection_drift_silent_when_contract_holds() -> None:
    report = ReadinessReport(ready=True)

    apply_protection_drift(report, _matching_payload(), "main")

    assert report.findings == []


def test_apply_protection_drift_skips_non_default_base() -> None:
    report = ReadinessReport(ready=True)

    apply_protection_drift(report, None, "feature/base-branch")

    assert report.findings == []
