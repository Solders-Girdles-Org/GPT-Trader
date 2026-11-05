"""Who the required status believes: RJ's linked words and the reviewer (RJ-1555).

GPT-Trader is public, so any signed-in GitHub user can comment or review. The
first three tests are Grok Scout's reproductions on main 9634b00, which posted
success before this check.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.scripts.test_trading_trigger_status import (
    HEAD,
    REPO,
    TRADING,
    FakeGitHub,
    install_fake,
    run,
)

RESERVED = "Reserved: e - places live orders"
REVIEW = f"https://github.com/{REPO}/pull/7#pullrequestreview-99"
ISSUE_WORDS = f"https://github.com/{REPO}/issues/3#issuecomment-5"


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = install_fake(monkeypatch)
    fake.files = [{"filename": TRADING}]
    return fake


def _verdict(github: FakeGitHub) -> dict[str, str]:
    assert run("--pr", "7", "--expected-head", HEAD) == 0
    [verdict] = github.posted
    return verdict


def _rj_comment(body: str = f"Yes to {HEAD[:7]}.", **extra: Any) -> dict[str, Any]:
    return {
        "issue_url": f"https://api.github.com/repos/{REPO}/issues/3",
        "user": {"login": "Solders-Girdles"},
        "body": body,
        **extra,
    }


def test_scout_link_to_another_repo_does_not_authorize(github: FakeGitHub) -> None:
    words = "https://github.com/nobody/nothing/issues/1#issuecomment-1"
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {words}"
    verdict = _verdict(github)
    assert verdict["state"] == "failure"
    assert "outside o/r" in verdict["description"]


def test_scout_linear_link_outside_rjs_workspace_does_not_authorize(github: FakeGitHub) -> None:
    words = "https://linear.app/x/issue/NOPE-0#comment-zzz"
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {words}"
    assert _verdict(github)["state"] == "failure"


def test_scout_commented_review_by_an_outsider_does_not_count(github: FakeGitHub) -> None:
    github.reviews = {99: HEAD}
    github.review_user = "someone-signed-in"
    github.pr["body"] = f"Reserved: none\nReview: {REVIEW} (Codex) at {HEAD[:7]}: clean"
    verdict = _verdict(github)
    assert verdict["state"] == "failure"
    assert "someone-signed-in" in verdict["description"]


def test_review_by_an_expected_account_still_counts(github: FakeGitHub) -> None:
    github.reviews = {99: HEAD}
    github.review_user = "solders-girdles"  # GitHub logins ignore case
    github.pr["body"] = f"Reserved: none\nReview: {REVIEW} (Codex) at {HEAD[:7]}: clean"
    assert _verdict(github)["state"] == "success"


def test_review_with_no_author_does_not_count(github: FakeGitHub) -> None:
    github.reviews = {99: HEAD}
    github.review_user = ""  # a deleted account shows as no login
    github.pr["body"] = f"Reserved: none\nReview: {REVIEW} (Codex) at {HEAD[:7]}: clean"
    assert _verdict(github)["state"] == "failure"


def test_rjs_issue_comment_naming_the_head_authorizes(github: FakeGitHub) -> None:
    github.comments["issues/comments/5"] = _rj_comment()
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {ISSUE_WORDS}"
    assert _verdict(github)["state"] == "success"


def test_a_full_sha_in_rjs_words_matches_a_short_line(github: FakeGitHub) -> None:
    github.comments["issues/comments/5"] = _rj_comment(f"Approved at {HEAD}")
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:9]} {ISSUE_WORDS}"
    assert _verdict(github)["state"] == "success"


def test_a_missing_comment_does_not_authorize(github: FakeGitHub) -> None:
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {ISSUE_WORDS}"
    verdict = _verdict(github)
    assert verdict["state"] == "failure"
    assert "no such comment" in verdict["description"]


@pytest.mark.parametrize(
    "text",
    [
        "Yes, go ahead.",
        f"Yes to {HEAD[:6]}.",  # too short to name a commit
        f"Yes to {HEAD[:7]}x.",  # not a SHA on its own
        "Yes to 1234567.",  # another head
    ],
)
def test_a_comment_that_does_not_name_the_head_does_not_authorize(
    github: FakeGitHub, text: str
) -> None:
    github.comments["issues/comments/5"] = _rj_comment(text)
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {ISSUE_WORDS}"
    verdict = _verdict(github)
    assert verdict["state"] == "failure"
    assert "doesn't name head" in verdict["description"]


def test_a_comment_by_an_outsider_does_not_authorize(github: FakeGitHub) -> None:
    github.comments["issues/comments/5"] = _rj_comment(user={"login": "someone-signed-in"})
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {ISSUE_WORDS}"
    verdict = _verdict(github)
    assert verdict["state"] == "failure"
    assert "not RJ's account" in verdict["description"]


def test_a_comment_on_another_issue_than_the_link_names_does_not_authorize(
    github: FakeGitHub,
) -> None:
    github.comments["issues/comments/5"] = _rj_comment(
        issue_url=f"https://api.github.com/repos/{REPO}/issues/4"
    )
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {ISSUE_WORDS}"
    assert _verdict(github)["state"] == "failure"


def test_rjs_review_comment_naming_the_head_authorizes(github: FakeGitHub) -> None:
    github.comments["pulls/comments/8"] = {
        "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/7",
        "user": {"login": "Solders-Girdles"},
        "body": f"OK for {HEAD[:7]}",
    }
    words = f"https://github.com/{REPO}/pull/7#discussion_r8"
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {words}"
    assert _verdict(github)["state"] == "success"


def test_rjs_review_naming_the_head_authorizes(github: FakeGitHub) -> None:
    github.reviews = {6: HEAD}
    github.review_bodies = {6: f"Approved {HEAD[:7]}"}
    words = f"https://github.com/{REPO}/pull/7#pullrequestreview-6"
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {words}"
    assert _verdict(github)["state"] == "success"
    github.posted.clear()
    github.review_bodies = {6: ""}
    assert _verdict(github)["state"] == "failure"


def test_every_github_link_on_the_line_must_hold(github: FakeGitHub) -> None:
    github.comments["issues/comments/5"] = _rj_comment()
    foreign = "https://github.com/nobody/nothing/issues/1#issuecomment-1"
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {ISSUE_WORDS} and {foreign}"
    assert _verdict(github)["state"] == "failure"


def test_a_line_hidden_in_an_html_comment_is_not_fetched(github: FakeGitHub) -> None:
    # It can't authorize anyway, so its link doesn't need to hold.
    foreign = "https://github.com/nobody/nothing/issues/1#issuecomment-1"
    github.pr["body"] = f"Reserved: none\n<!--\nRJ-authorized: {HEAD[:7]} {foreign}\n-->"
    github.files = [{"filename": "docs/STATUS.md"}]
    assert _verdict(github)["state"] == "success"


def test_a_comment_github_will_not_show_posts_error(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_call = FakeGitHub.__call__

    def forbidden(self: FakeGitHub, args: list[str], **kwargs: Any) -> str:
        if "/comments/" in args[1]:
            raise RuntimeError("gh: Resource not accessible by integration (HTTP 403)")
        return fake_call(self, args, **kwargs)

    monkeypatch.setattr(FakeGitHub, "__call__", forbidden)
    github.pr["body"] = f"{RESERVED}\nRJ-authorized: {HEAD[:7]} {ISSUE_WORDS}"
    assert run("--pr", "7", "--expected-head", HEAD) == 1
    assert [p["state"] for p in github.posted] == ["error"]
