"""RJ's authorization recorded in the PR body (``RJ-authorized: <head SHA> <link>``)."""

from __future__ import annotations

import pytest

from gpt_trader.agents import trading_trigger as tt

HEAD = "abcdef1234567890"
TRADING = ["src/gpt_trader/features/live_trade/execution/order_submission.py"]
RESERVED = "Reserved: e - can cancel open orders"
WORDS = "https://linear.app/rj-openclaw/issue/RJ-1234#comment-abc"


@pytest.mark.parametrize(
    "line",
    [
        f"RJ-authorized: abcdef1 {WORDS}",
        f"RJ-authorized: abcdef1234567890 (RJ on RJ-1234: 'go ahead', {WORDS})",
        "- **RJ-authorized:** ABCDEF1, carried from 1234567, "
        "https://github.com/o/r/pull/1#issuecomment-9",
        f"rj-authorized: abcdef12 {WORDS}",
    ],
)
def test_authorization_line_for_this_head_clears_reserved(line: str) -> None:
    assert tt.assess(TRADING, f"{RESERVED}\n{line}", HEAD).severity == "info"


@pytest.mark.parametrize(
    "line",
    [
        f"RJ-authorized: 1234567 {WORDS}",  # another head
        f"RJ-authorized: abc {WORDS}",  # too short to name a head
        f"RJ-authorized: abcdef1x {WORDS}",  # not a SHA
        f"RJ-authorized: yes {WORDS}",
        "RJ-authorized:",
        f"RJ authorized abcdef1 {WORDS}",  # not the line
        f"Note that RJ-authorized: abcdef1 {WORDS}",  # not at the start of a line
        "RJ-authorized: abcdef1",  # no link to RJ's words
        "RJ-authorized: abcdef1 RJ said yes in chat",
        "RJ-authorized: abcdef1 http://linear.app/x",  # not https
        "RJ-authorized: abcdef1 https://example.com/rj-said-yes",  # not Linear or GitHub
        f"{WORDS} RJ-authorized: abcdef1",
    ],
)
def test_authorization_line_that_does_not_count_blocks(line: str) -> None:
    assert tt.assess(TRADING, f"{RESERVED}\n{line}", HEAD).severity == "blocker"


def test_authorization_for_an_earlier_head_does_not_carry() -> None:
    body = f"{RESERVED}\nRJ-authorized: abcdef1 {WORDS}"
    assert tt.assess(TRADING, body, "1234567890abcdef").severity == "blocker"


def test_any_authorization_line_naming_this_head_counts() -> None:
    body = f"{RESERVED}\nRJ-authorized: 1234567 {WORDS}\nRJ-authorized: abcdef1 {WORDS}"
    assert tt.assess(TRADING, body, HEAD).severity == "info"


def test_authorization_does_not_replace_review_for_reserved_none() -> None:
    # Authorization clears a declared reservation; it is not a review of a
    # `Reserved: none` claim on trading paths.
    body = f"Reserved: none\nRJ-authorized: abcdef1 {WORDS}"
    assert tt.assess(TRADING, body, HEAD).severity == "blocker"


def test_authorization_does_not_stand_in_for_a_reserved_line() -> None:
    body = f"RJ-authorized: abcdef1 {WORDS}"
    assert tt.assess(["docs/STATUS.md"], body, HEAD).severity == "blocker"


def test_blocker_names_the_line_to_add() -> None:
    message = tt.assess(TRADING, RESERVED, HEAD).message
    assert "`RJ-authorized: abcdef1 <link to his words>`" in message


def test_rj_authorized_heads_reads_raw_lines() -> None:
    body = (
        f"```\nRJ-authorized: ABCDEF1 {WORDS}\n```\n- RJ-authorized: 1234567 later, {WORDS}\n"
        f"RJ-authorized: nope {WORDS}"
    )
    assert tt.rj_authorized_heads(body) == ["abcdef1", "1234567"]


@pytest.mark.parametrize(
    "body",
    [
        f"<!--\nRJ-authorized: abcdef1 {WORDS}\n-->",
        f"<!-- RJ-authorized: abcdef1 {WORDS} -->",
        f"Notes\n<!-- unclosed\nRJ-authorized: abcdef1 {WORDS}",
        f"<!-- a -->\n<!--\nRJ-authorized: abcdef1 {WORDS}\n-->",
    ],
)
def test_authorization_hidden_in_an_html_comment_does_not_count(body: str) -> None:
    assert tt.rj_authorized_heads(body) == []
    assert tt.assess(TRADING, f"{RESERVED}\n{body}", HEAD).severity == "blocker"


def test_authorization_after_a_closed_html_comment_counts() -> None:
    body = f"<!-- template note -->\nRJ-authorized: abcdef1 {WORDS}"
    assert tt.rj_authorized_heads(body) == ["abcdef1"]


def test_reserved_line_inside_an_html_comment_still_counts() -> None:
    # Hiding a reservation must not make a PR routine.
    body = "Reserved: none\n<!--\nReserved: e - places orders\n-->"
    assert tt.assess(["docs/STATUS.md"], body, HEAD).severity == "blocker"


LINK = "https://github.com/o/r/pull/7#pullrequestreview-11"


def test_review_claims_name_each_review_and_its_commit() -> None:
    body = (
        f"Review: {LINK} (Codex) at 1234567: blocking\n"
        f"Review: {LINK} (Codex) at 2345678: clean\n"
        f"Review: {LINK} (Codex) at abcdef1: clean (carried from 2345678: clean rebase)\n"
        "Review: none\n"
        "Review: https://github.com/o/r/pull/8#pullrequestreview-12 (Codex) at abcdef1: clean\n"
        f"Review: {LINK} (Codex) at abcdef1: clean-ish"
    )
    assert tt.review_claims(body, "o/r", 7) == [(11, "1234567"), (11, "2345678"), (11, "2345678")]


def test_gate_runner_files_are_flagged() -> None:
    runner = [
        ".github/workflows/trading-trigger.yml",
        "scripts/ci/trading_trigger_status.py",
        "tests/unit/scripts/test_trading_trigger_status.py",
        "tests/unit/gpt_trader/agents/test_trading_trigger_authorized.py",
    ]
    assert tt.trading_paths(runner) == sorted(runner)
