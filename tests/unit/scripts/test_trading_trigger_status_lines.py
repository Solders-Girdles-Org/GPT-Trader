"""The required ``trading-trigger/base`` status: Review and RJ-authorized lines, and pending."""

from __future__ import annotations

from typing import Any

import pytest
from scripts.ci import trading_trigger_status as status

from tests.unit.scripts.test_trading_trigger_status import (
    HEAD,
    LINK,
    QUEUE_REF,
    QUEUE_SHA,
    TRADING,
    FakeGitHub,
    FlakyGitHub,
    _queue_ref,
    install_fake,
    run,
)


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    return install_fake(monkeypatch)


def test_clean_review_of_this_head_posts_success(github: FakeGitHub) -> None:
    github.files = [{"filename": TRADING}]
    github.reviews = {1: HEAD}
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Codex) at {HEAD[:7]}: clean"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "success"


@pytest.mark.parametrize(
    "reviews",
    [{}, {1: "f" * 40}, {2: HEAD}],
    ids=["no such review", "review of another commit", "another review id"],
)
def test_review_line_must_name_a_real_review_of_that_commit(
    github: FakeGitHub, reviews: dict[int, str]
) -> None:
    github.files = [{"filename": TRADING}]
    github.reviews = reviews
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Codex) at {HEAD[:7]}: clean"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"
    assert "no review 1 of abcdef1" in github.posted[0]["description"]


def test_review_of_another_commit_sharing_the_heads_prefix_fails(github: FakeGitHub) -> None:
    # A review of abcdef1...111 must not vouch for head abcdef1...000.
    head = "abcdef1" + "0" * 33
    github.pr["head"] = {"sha": head}
    github.files = [{"filename": TRADING}]
    github.reviews = {1: "abcdef1" + "1" * 33}
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Codex) at abcdef1: clean"
    run("--pr", "7", "--expected-head", head)
    assert github.posted[0]["state"] == "failure"


@pytest.mark.parametrize(
    ("state", "verdict"),
    [
        ("APPROVED", "success"),
        ("COMMENTED", "success"),
        ("CHANGES_REQUESTED", "failure"),
        ("DISMISSED", "failure"),
        ("PENDING", "failure"),
    ],
)
def test_clean_claim_needs_a_review_state_that_is_clean(
    github: FakeGitHub, state: str, verdict: str
) -> None:
    github.files = [{"filename": TRADING}]
    github.reviews = {1: HEAD}
    github.review_state = state
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Codex) at {HEAD[:7]}: clean"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == verdict


def test_blocking_claim_may_name_a_changes_requested_review(github: FakeGitHub) -> None:
    old = "1234567890abcdef1234567890abcdef12345678"
    github.reviews = {1: old}
    github.review_state = "CHANGES_REQUESTED"
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Codex) at {old[:7]}: blocking"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "success"


WORDS = "https://linear.app/rj-openclaw/issue/RJ-1#comment-1"


def test_authorization_hidden_by_markdown_does_not_count(github: FakeGitHub) -> None:
    # GitHub renders a link-reference title as nothing, so the line is unseen.
    github.files = [{"filename": TRADING}]
    github.pr["body"] = (
        f'Reserved: e - places orders\n\n[x]: https://example.org/ "\n'
        f'RJ-authorized: {HEAD[:7]} {WORDS}\n"'
    )
    github.rendered = "<p>Reserved: e - places orders</p>"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"


def test_shown_prose_naming_the_head_does_not_vouch_for_a_hidden_line(
    github: FakeGitHub,
) -> None:
    github.files = [{"filename": TRADING}]
    github.pr["body"] = (
        f"Reserved: e - places orders\n\nDo not add RJ-authorized: {HEAD[:7]} until RJ approves."
        f'\n\n[x]: https://example.org/ "\nRJ-authorized: {HEAD[:7]} {WORDS}\n"'
    )
    github.rendered = (
        "<p>Reserved: e - places orders</p>\n"
        f"<p>Do not add RJ-authorized: {HEAD[:7]} until RJ approves.</p>"
    )
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"


def test_rendered_lines_split_blocks_and_breaks_and_keep_link_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Html:
        def __call__(self, args: list[str], stdin: str | None = None) -> str:
            return (
                "<p>Reserved: e<br>Self-check: x</p><ul><li><strong>RJ-authorized:</strong> "
                '1234567 <a href="https://linear.app/a">RJ</a></li></ul>'
                "<pre><code>one\ntwo</code></pre><!-- hidden -->"
            )

    monkeypatch.setattr(status, "_gh", Html())
    assert status.rendered_lines("o/r", "body") == [
        "Reserved: e",
        "Self-check: x",
        "RJ-authorized: 1234567 RJ https://linear.app/a",
        "one",
        "two",
    ]


def test_authorization_with_githubs_shortened_commit_link_counts(github: FakeGitHub) -> None:
    # GitHub renders a real commit SHA as a link showing its first 7 characters.
    github.files = [{"filename": TRADING}]
    github.pr["body"] = f"Reserved: e - places orders\nRJ-authorized: {HEAD} {WORDS}"
    commit = f"https://github.com/o/r/commit/{HEAD}"
    github.rendered = (
        f'<p dir="auto">Reserved: e - places orders<br>\nRJ-authorized: <a class="commit-link" '
        f'href="{commit}"><tt>{HEAD[:7]}</tt></a> <a href="{WORDS}">{WORDS}</a></p>'
    )
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "success"


def test_a_commit_link_is_not_rjs_words(github: FakeGitHub) -> None:
    commit = f"https://github.com/o/r/commit/{HEAD}"
    github.files = [{"filename": TRADING}]
    github.pr["body"] = f"Reserved: e - places orders\nRJ-authorized: {HEAD[:7]} {commit}"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"


def test_authorization_shown_in_the_rendered_pr_counts(github: FakeGitHub) -> None:
    github.files = [{"filename": TRADING}]
    github.pr["body"] = (
        f"Reserved: e - places orders\n- **RJ-authorized:** {HEAD[:7]} [RJ]({WORDS})"
    )
    github.rendered = (
        f"<p>Reserved: e - places orders</p><ul><li><strong>RJ-authorized:</strong> "
        f'{HEAD[:7].upper()} <a href="{WORDS}">RJ</a></li></ul>'
    )
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "success"


def test_rendering_is_only_asked_for_an_authorization_line(github: FakeGitHub) -> None:
    run("--pr", "7", "--expected-head", HEAD)
    assert github.markdown_calls == 0


def test_error_after_pending_replaces_it_when_the_head_was_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without --expected-head the runner learns the head itself; an error must still land there.
    fake = FlakyGitHub("--paginate")
    monkeypatch.setattr(status, "_gh", fake)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert run("--pr", "7") == 1
    assert fake.pending == [HEAD]
    assert [(p["sha"], p["state"]) for p in fake.posted] == [(HEAD, "error")]


def test_carried_review_is_checked_against_the_head_it_was_carried_from(
    github: FakeGitHub,
) -> None:
    old = "1234567890abcdef1234567890abcdef12345678"
    github.files = [{"filename": TRADING}]
    github.reviews = {1: old}
    github.pr["body"] = (
        f"Reserved: none\nReview: {LINK} (Codex) at {old[:7]}: clean\n"
        f"Review: {LINK} (Codex) at {HEAD[:7]}: clean (carried from {old[:7]}: clean rebase)"
    )
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "success"


def test_any_unbacked_review_line_fails_even_off_trading_paths(github: FakeGitHub) -> None:
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Claude) at {HEAD[:7]}: clean"
    run("--pr", "7", "--expected-head", HEAD)
    assert github.posted[0]["state"] == "failure"


def test_review_lookup_error_posts_error(github: FakeGitHub, monkeypatch) -> None:
    def broken(repo: str, number: int, review_id: int) -> None:
        raise RuntimeError("HTTP 502")

    monkeypatch.setattr(status, "fetch_review", broken)
    github.pr["body"] = f"Reserved: none\nReview: {LINK} (Claude) at {HEAD[:7]}: clean"
    assert run("--pr", "7", "--expected-head", HEAD) == 1
    assert github.posted[0]["state"] == "error"


def test_pending_goes_first_on_the_head_and_queued_copies(github: FakeGitHub) -> None:
    # A run killed after this leaves pending, not an earlier success.
    github.queue_refs = [_queue_ref(7, QUEUE_SHA)]
    run("--pr", "7", "--expected-head", HEAD)
    assert github.pending == [HEAD, QUEUE_SHA]
    assert github.order == ["pending", "pending", "success", "success"]


def test_queue_run_marks_its_commit_pending_first(github: FakeGitHub) -> None:
    run("--merge-group-ref", QUEUE_REF, "--status-sha", QUEUE_SHA)
    assert github.pending == [QUEUE_SHA]
    assert github.order[0] == "pending"


@pytest.mark.parametrize("pr", [{"state": "closed"}, {"head": {"sha": "f" * 40}}])
def test_obsolete_event_marks_nothing_pending(github: FakeGitHub, pr: dict[str, Any]) -> None:
    github.pr.update(pr)
    run("--pr", "7", "--expected-head", HEAD)
    assert github.pending == []
