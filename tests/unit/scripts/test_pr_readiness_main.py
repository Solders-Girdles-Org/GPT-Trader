from __future__ import annotations

import json
from typing import Any

import scripts.agents.pr_readiness as pr_readiness


def test_main_uses_pr_base_for_branch_protection(monkeypatch) -> None:
    bases: list[str] = []

    monkeypatch.setattr(pr_readiness, "fetch_review_threads", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_pr_files", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_pr_reactions", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_update_events", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_commit_timestamps", lambda repo, pr: (None, None))
    monkeypatch.setattr(
        pr_readiness,
        "fetch_pr_payload",
        lambda repo, pr: {
            "number": 12,
            "headRefOid": "abcdef1234",
            "baseRefName": "release/2026-06",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "statusCheckRollup": [],
        },
    )

    def fake_protection(repo: str, branch: str) -> dict[str, Any]:
        bases.append(branch)
        return {"required_status_checks": {"checks": []}}

    monkeypatch.setattr(pr_readiness, "fetch_branch_protection", fake_protection)

    assert pr_readiness.main(["--repo", "owner/repo", "--pr", "12"]) == 0
    assert bases == ["release/2026-06"]


def test_main_reports_missing_current_head_review_signal_by_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr(pr_readiness, "fetch_review_threads", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_pr_files", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_pr_reactions", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_update_events", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_commit_timestamps", lambda repo, pr: (None, None))
    monkeypatch.setattr(pr_readiness, "fetch_branch_protection", lambda repo, branch: {})
    monkeypatch.setattr(
        pr_readiness,
        "fetch_pr_payload",
        lambda repo, pr: {
            "number": 12,
            "headRefOid": "abcdef1234",
            "baseRefName": "main",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "statusCheckRollup": [],
        },
    )

    assert pr_readiness.main(["--repo", "owner/repo", "--pr", "12"]) == 0
    output = capsys.readouterr().out
    assert "verdict: READY" in output
    assert "[warning]" in output
    assert "No current-head review" in output


def test_main_requires_current_head_review_signal_when_requested(monkeypatch, capsys) -> None:
    monkeypatch.setattr(pr_readiness, "fetch_review_threads", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_pr_files", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_pr_reactions", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_update_events", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_commit_timestamps", lambda repo, pr: (None, None))
    monkeypatch.setattr(pr_readiness, "fetch_branch_protection", lambda repo, branch: {})
    monkeypatch.setattr(
        pr_readiness,
        "fetch_pr_payload",
        lambda repo, pr: {
            "number": 12,
            "headRefOid": "abcdef1234",
            "baseRefName": "main",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "statusCheckRollup": [],
        },
    )

    assert (
        pr_readiness.main(
            ["--repo", "owner/repo", "--pr", "12", "--require-current-head-review-signal"]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "verdict: NOT READY" in output
    assert "[blocker]" in output


def test_main_exit_on_not_ready_fails_when_github_unavailable(monkeypatch) -> None:
    def raise_unavailable() -> str:
        raise RuntimeError("gh missing")

    monkeypatch.setattr(pr_readiness, "detect_repo", raise_unavailable)

    assert pr_readiness.main(["--exit-on-not-ready"]) == 1


def test_main_json_format_survives_github_unavailable(monkeypatch, capsys) -> None:
    def raise_unavailable() -> str:
        raise RuntimeError("gh missing")

    monkeypatch.setattr(pr_readiness, "detect_repo", raise_unavailable)

    assert pr_readiness.main(["--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
    assert payload["verdict"] == "NOT READY"


def _stub_github(monkeypatch, *, files: list[str], body: str) -> None:
    monkeypatch.setattr(pr_readiness, "fetch_review_threads", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_pr_files", lambda repo, pr: files)
    monkeypatch.setattr(pr_readiness, "fetch_pr_reactions", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_update_events", lambda repo, pr: [])
    monkeypatch.setattr(pr_readiness, "fetch_head_commit_timestamps", lambda repo, pr: (None, None))
    monkeypatch.setattr(pr_readiness, "fetch_branch_protection", lambda repo, branch: {})
    monkeypatch.setattr(
        pr_readiness,
        "fetch_pr_payload",
        lambda repo, pr: {
            "number": 12,
            "headRefOid": "abcdef1234",
            "baseRefName": "main",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "statusCheckRollup": [],
            "body": body,
        },
    )


def test_main_blocks_trading_paths_without_confirmed_reserved_line(monkeypatch, capsys) -> None:
    _stub_github(
        monkeypatch,
        files=["src/gpt_trader/features/live_trade/execution/order_submission.py"],
        body="Reserved: none\nSelf-check: read the diff",
    )

    assert pr_readiness.main(["--repo", "owner/repo", "--pr", "12", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
    assert payload["pull_request"]["trading_paths"] == [
        "src/gpt_trader/features/live_trade/execution/order_submission.py"
    ]
    assert any("Trading or broker paths" in f["message"] for f in payload["findings"])


def test_main_rj_authorized_head_clears_reserved_blocker(monkeypatch, capsys) -> None:
    _stub_github(
        monkeypatch,
        files=["src/gpt_trader/features/brokerages/coinbase/rest/order_service.py"],
        body="Reserved: e - changes how orders are cancelled",
    )

    assert pr_readiness.main(["--repo", "owner/repo", "--pr", "12"]) == 0
    assert "verdict: NOT READY" in capsys.readouterr().out

    assert (
        pr_readiness.main(["--repo", "owner/repo", "--pr", "12", "--rj-authorized-head", "abcdef1"])
        == 0
    )
    output = capsys.readouterr().out
    assert "verdict: READY" in output
    assert "RJ authorized head abcdef1" in output


def test_main_routine_pr_stays_ready(monkeypatch, capsys) -> None:
    _stub_github(monkeypatch, files=["docs/STATUS.md", "uv.lock"], body="Reserved: none")

    assert pr_readiness.main(["--repo", "owner/repo", "--pr", "12"]) == 0
    output = capsys.readouterr().out
    assert "verdict: READY" in output
    assert "no trading or broker paths" in output
