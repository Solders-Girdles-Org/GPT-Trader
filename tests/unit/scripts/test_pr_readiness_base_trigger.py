"""RJ-1118: agent-pr-ready also judges a PR with the base branch's trigger."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import scripts.agents.pr_readiness as pr_readiness
from scripts.agents.pr_readiness import (
    BranchProtection,
    Finding,
    PullRequestState,
    assess_readiness,
)

REPO = Path(__file__).resolve().parents[3]
SOURCE = (REPO / pr_readiness.TRIGGER_PATH).read_text()
LIVE = "src/gpt_trader/features/brokerages/coinbase/client/orders.py"


def _state(paths: list[str], body: str = "Reserved: none") -> PullRequestState:
    return PullRequestState(
        number=7,
        head_oid="abcdef1234567",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(),
        threads=(),
        protection=BranchProtection(False, False, 0, ()),
        body=body,
        changed_paths=tuple(paths),
    )


def test_base_trigger_matches_the_checkout_trigger() -> None:
    for paths in ([LIVE], ["docs/STATUS.md"]):
        state = _state(paths)
        base = pr_readiness.run_base_trigger(SOURCE, state, None, "o/r")
        head = pr_readiness.trading_trigger.assess(paths, "Reserved: none", "abcdef1234567")
        assert base.severity == head.severity


def test_a_stricter_base_verdict_holds_the_pr(monkeypatch) -> None:
    # The PR narrowed its own trigger, so the checkout's copy says info; the
    # base branch's copy still says blocker, and that holds the PR.
    narrowed = SOURCE + "\n\ndef trading_paths(paths):\n    return []\n"
    assert pr_readiness.run_base_trigger(narrowed, _state([LIVE]), None, "o/r").severity == "info"

    result = pr_readiness.trading_trigger.TriggerResult("info", "no trading paths")

    class Narrowed:
        @staticmethod
        def assess(*args, **kwargs):
            return result

    monkeypatch.setattr(pr_readiness, "trading_trigger", Narrowed)
    base = pr_readiness.run_base_trigger(SOURCE, _state([LIVE]), None, "o/r")
    assert base.severity == "blocker"
    report = assess_readiness(_state([LIVE]), base_trigger=base)
    assert not report.ready
    assert any(f.message.startswith("Base-branch trading trigger") for f in report.findings)


def test_a_looser_base_verdict_adds_nothing() -> None:
    state = _state([LIVE])
    report = assess_readiness(state, base_trigger=Finding("info", "Base-branch trading trigger: x"))
    assert not any(f.message.startswith("Base-branch") for f in report.findings)


def test_base_trigger_runs_without_the_project_on_the_path() -> None:
    # -I -S: nothing from the checkout (or site-packages) is importable.
    probe = SOURCE + "\nimport gpt_trader\n"
    finding = pr_readiness.run_base_trigger(probe, _state([LIVE]), None, "o/r")
    assert finding.severity == "blocker"
    assert "ModuleNotFoundError" in finding.message


def test_broken_base_trigger_fails_closed() -> None:
    finding = pr_readiness.run_base_trigger("raise SystemExit(0)", _state([LIVE]), None, "o/r")
    assert finding.severity == "blocker"


def test_failing_base_trigger_is_not_clean_even_with_a_verdict() -> None:
    source = (
        "import json\n"
        'print(json.dumps({"severity": "info", "message": "looks clean"}), flush=True)\n'
        "raise SystemExit(1)\n"
    )
    finding = pr_readiness.run_base_trigger(source, _state([LIVE]), None, "o/r")
    assert finding.severity == "blocker"
    assert "exited 1" in finding.message


def test_unreadable_base_trigger_blocks_main(monkeypatch, capsys) -> None:
    for name, value in {
        "fetch_review_threads": lambda repo, pr: [],
        "fetch_pr_files": lambda repo, pr: ["docs/STATUS.md"],
        "fetch_pr_reactions": lambda repo, pr: [],
        "fetch_head_update_events": lambda repo, pr: [],
        "fetch_head_commit_timestamps": lambda repo, pr: (None, None),
        "fetch_branch_protection": lambda repo, branch: {},
        "fetch_merge_queue_active": lambda repo, branch: False,
        "fetch_pr_payload": lambda repo, pr: {
            "number": 12,
            "headRefOid": "abcdef1234",
            "baseRefName": "main",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "statusCheckRollup": [],
            "body": "Reserved: none",
        },
    }.items():
        monkeypatch.setattr(pr_readiness, name, value)

    def unreadable(repo: str, base: str) -> str:
        raise RuntimeError("404")

    monkeypatch.setattr(pr_readiness, "fetch_base_trigger_source", unreadable)
    pr_readiness.main(["--repo", "o/r", "--pr", "12"])
    out = capsys.readouterr().out
    assert "Could not read the base branch's trigger: 404" in out
    assert "verdict: READY" not in out


def test_readiness_does_not_import_the_app_package() -> None:
    # Importing gpt_trader would run its __init__ (CLI and most of the app),
    # any of which could patch the trigger in-process.
    code = (
        "import runpy, sys; runpy.run_path(sys.argv[1], run_name='probe'); "
        "print(sorted(m for m in sys.modules if m == 'gpt_trader' or m.startswith('gpt_trader.')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(REPO / "scripts/agents/pr_readiness.py")],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout
    assert out.strip() == "[]"
