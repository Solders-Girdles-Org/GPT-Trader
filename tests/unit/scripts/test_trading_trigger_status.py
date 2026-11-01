"""The required ``trading-trigger/base`` status, run from base-branch code."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import trading_trigger_status as status

REPO = "o/r"
HEAD = "abcdef1234567890abcdef1234567890abcdef12"
QUEUE_SHA = "1111111111111111111111111111111111111111"
QUEUE_REF = "refs/heads/gh-readonly-queue/main/pr-7-2222222222222222222222222222222222222222"
TRADING = "src/gpt_trader/features/live_trade/execution/order_submission.py"
LINK = f"https://github.com/{REPO}/pull/7#pullrequestreview-1"
WORKFLOW = status.ROOT / ".github" / "workflows" / "trading-trigger.yml"


class FakeGitHub:
    """Answers the runner's gh calls and records the statuses it posts."""

    def __init__(self, body: str, files: list[str], **pr: Any) -> None:
        self.pr = {"number": 7, "state": "open", "head": {"sha": HEAD}, "body": body}
        self.pr["changed_files"] = len(files)
        self.pr.update(pr)
        self.files = [{"filename": f} for f in files]
        self.heads: list[str] = []  # head returned by each later pulls read
        self.posted: list[dict[str, str]] = []

    def __call__(self, args: list[str]) -> str:
        if args[:3] == ["api", "--method", "POST"]:
            fields = dict(a.split("=", 1) for a in args[4:] if a != "-f")
            fields["sha"] = args[3].rsplit("/", 1)[1]
            self.posted.append(fields)
            return "{}"
        if "--paginate" in args:
            return json.dumps([self.files])
        pr = dict(self.pr)
        if self.heads:
            pr["head"] = {"sha": self.heads.pop(0)}
        return json.dumps(pr)


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub("Reserved: none", ["docs/STATUS.md"])
    monkeypatch.setattr(status, "_gh", fake)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return fake


def run(*args: str) -> int:
    return status.main(["--repo", REPO, *args])


def test_routine_pr_posts_success_on_its_head(github: FakeGitHub) -> None:
    assert run("--pr", "7", "--expected-head", HEAD, "--target-url", "https://run") == 0
    [post] = github.posted
    assert post["sha"] == HEAD
    assert post["state"] == "success"
    assert post["context"] == "trading-trigger/base"
    assert post["target_url"] == "https://run"


def test_trading_path_with_unconfirmed_none_posts_failure(github: FakeGitHub) -> None:
    github.files = [{"filename": TRADING}]
    assert run("--pr", "7", "--expected-head", HEAD) == 0
    assert github.posted[0]["state"] == "failure"
    assert len(github.posted[0]["description"]) <= 140


def test_clean_review_of_this_head_posts_success(github: FakeGitHub) -> None:
    github.files = [{"filename": TRADING}]
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Codex) at {HEAD[:7]}: clean"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "success"


def test_review_link_must_be_on_this_pr(github: FakeGitHub) -> None:
    github.files = [{"filename": TRADING}]
    other = f"https://github.com/{REPO}/pull/8#pullrequestreview-1"
    github.pr["body"] = f"Reserved: none\nReview: {other} (Codex) at {HEAD[:7]}: clean"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"


def test_reserved_pr_passes_only_with_rj_authorization_for_this_head(github: FakeGitHub) -> None:
    github.files = [{"filename": TRADING}]
    github.pr["body"] = "Reserved: e - cancels orders"
    run("--pr", "7", "--expected-head", HEAD)
    github.pr["body"] += f"\nRJ-authorized: {HEAD[:7]}"
    run("--pr", "7", "--expected-head", HEAD)
    assert [p["state"] for p in github.posted] == ["failure", "success"]


def test_truncated_file_list_fails(github: FakeGitHub) -> None:
    github.pr["changed_files"] = 3001
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"
    assert "1 of 3001" in github.posted[0]["description"]


def test_renamed_file_counts_both_paths(github: FakeGitHub) -> None:
    github.files = [{"filename": "src/gpt_trader/moved.py", "previous_filename": TRADING}]
    github.pr["body"] = "Reserved: none"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"


@pytest.mark.parametrize("pr", [{"state": "closed"}, {"head": {"sha": "f" * 40}}])
def test_obsolete_event_posts_nothing(github: FakeGitHub, pr: dict[str, Any]) -> None:
    github.pr.update(pr)
    assert run("--pr", "7", "--expected-head", HEAD) == 0
    assert github.posted == []


def test_head_moving_while_files_are_read_posts_nothing(github: FakeGitHub) -> None:
    github.heads = [HEAD, "f" * 40]
    assert run("--pr", "7", "--expected-head", HEAD) == 0
    assert github.posted == []


def test_merge_group_judges_the_queued_pr_and_posts_on_the_group(github: FakeGitHub) -> None:
    github.files = [{"filename": TRADING}]
    assert run("--merge-group-ref", QUEUE_REF, "--status-sha", QUEUE_SHA) == 0
    assert github.posted[0]["sha"] == QUEUE_SHA
    assert github.posted[0]["state"] == "failure"


def test_merge_group_head_moving_fails(github: FakeGitHub) -> None:
    github.heads = [HEAD, "f" * 40]
    run("--merge-group-ref", QUEUE_REF, "--status-sha", QUEUE_SHA)
    assert github.posted[0]["state"] == "failure"


@pytest.mark.parametrize(
    "ref", ["refs/heads/main", "gh-readonly-queue/main/pr-x-" + "2" * 40, "pr-7-" + "2" * 40]
)
def test_unrecognised_queue_branch_posts_error(github: FakeGitHub, ref: str) -> None:
    assert run("--merge-group-ref", ref, "--status-sha", QUEUE_SHA) == 1
    assert github.posted[0]["state"] == "error"


def test_failure_to_run_posts_error_instead_of_leaving_an_old_success(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(path: Path = status.TRIGGER_PATH) -> Any:
        raise ImportError("trigger missing")

    monkeypatch.setattr(status, "load_trigger", broken)
    assert run("--pr", "7", "--expected-head", HEAD) == 1
    assert github.posted[0] == {
        "state": "error",
        "context": "trading-trigger/base",
        "description": "Trading trigger failed to run: trigger missing",
        "sha": HEAD,
    }


def test_print_only_posts_nothing(github: FakeGitHub, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--pr", "7", "--print-only") == 0
    assert github.posted == []
    assert "success" in capsys.readouterr().out


def test_summary_quotes_pr_controlled_text(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    github.files = [{"filename": "config/<img src=x>.yaml"}]
    github.pr["changed_files"] = 1
    run("--pr", "7", "--expected-head", HEAD)
    # Indented and JSON-quoted: a Markdown code block, so no markup renders.
    lines = summary.read_text().splitlines()
    assert '    paths: ["config/<img src=x>.yaml"]' in lines
    assert all(line.startswith("    ") for line in lines if "<img" in line)


def test_description_fits_github_limit() -> None:
    assert len(status.description("x " * 200)) == 140
    assert status.description("a `b`  c") == "a b c"


def test_loads_the_trigger_file_it_is_given(tmp_path: Path) -> None:
    fake = tmp_path / "trading_trigger.py"
    fake.write_text("def assess(*args, **kwargs):\n    return 'from this file'\n")
    assert status.load_trigger(fake).assess() == "from this file"


def test_runs_isolated_without_the_package(tmp_path: Path) -> None:
    # The workflow runs `python3 -I -S`; the runner must not need site-packages
    # or the gpt_trader package import.
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(status.ROOT / "scripts/ci/trading_trigger_status.py")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 2  # argparse: --pr or --merge-group-ref is required
    assert "ModuleNotFoundError" not in result.stderr


def test_workflow_never_fetches_the_pr_head_or_uses_secrets() -> None:
    text = WORKFLOW.read_text()
    assert "pull_request_target:" in text and "merge_group:" in text
    assert "${{ secrets" not in text
    # No third-party action runs with the write token.
    assert not re.search(r"^\s*(?:- )?uses:", text, re.MULTILINE)
    assert "head.ref" not in text and "merge_group.head_sha" in text
    # The head SHA is passed only as the event's expected head, never fetched.
    assert re.findall(r"pull_request\.head\.\w+", text) == ["pull_request.head.sha"]
    assert "PR_HEAD: ${{ github.event.pull_request.head.sha }}" in text
    fetch = [line for line in text.splitlines() if "git -C base fetch" in line]
    assert fetch == [
        '          git -C base fetch -q --depth 1 --no-tags "https://github.com/$REPO" "$BASE_REF"'
    ]
    assert (
        "BASE_REF: ${{ github.event_name == 'merge_group' && "
        "github.event.merge_group.base_ref || github.sha }}" in text
    )
    assert "python3 -I -S scripts/ci/trading_trigger_status.py" in text
    assert "statuses: write" in text and "contents: write" not in text
