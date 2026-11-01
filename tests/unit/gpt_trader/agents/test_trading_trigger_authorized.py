"""RJ's authorization recorded in the PR body (``RJ-authorized: <head SHA>``)."""

from __future__ import annotations

import pytest

from gpt_trader.agents import trading_trigger as tt

HEAD = "abcdef1234567890"
TRADING = ["src/gpt_trader/features/live_trade/execution/order_submission.py"]
RESERVED = "Reserved: e - can cancel open orders"


@pytest.mark.parametrize(
    "line",
    [
        "RJ-authorized: abcdef1",
        "RJ-authorized: abcdef1234567890 (RJ on RJ-1234: 'go ahead')",
        "- **RJ-authorized:** ABCDEF1, carried from 1234567",
        "rj-authorized: abcdef12",
    ],
)
def test_authorization_line_for_this_head_clears_reserved(line: str) -> None:
    assert tt.assess(TRADING, f"{RESERVED}\n{line}", HEAD).severity == "info"


@pytest.mark.parametrize(
    "line",
    [
        "RJ-authorized: 1234567",  # another head
        "RJ-authorized: abc",  # too short to name a head
        "RJ-authorized: abcdef1x",  # not a SHA
        "RJ-authorized: yes",
        "RJ-authorized:",
        "RJ authorized abcdef1",  # not the line
        "Note that RJ-authorized: abcdef1",  # not at the start of a line
    ],
)
def test_authorization_line_that_does_not_name_this_head_blocks(line: str) -> None:
    assert tt.assess(TRADING, f"{RESERVED}\n{line}", HEAD).severity == "blocker"


def test_authorization_for_an_earlier_head_does_not_carry() -> None:
    body = f"{RESERVED}\nRJ-authorized: abcdef1"
    assert tt.assess(TRADING, body, "1234567890abcdef").severity == "blocker"


def test_any_authorization_line_naming_this_head_counts() -> None:
    body = f"{RESERVED}\nRJ-authorized: 1234567\nRJ-authorized: abcdef1"
    assert tt.assess(TRADING, body, HEAD).severity == "info"


def test_authorization_does_not_replace_review_for_reserved_none() -> None:
    # Authorization clears a declared reservation; it is not a review of a
    # `Reserved: none` claim on trading paths.
    body = "Reserved: none\nRJ-authorized: abcdef1"
    assert tt.assess(TRADING, body, HEAD).severity == "blocker"


def test_authorization_does_not_stand_in_for_a_reserved_line() -> None:
    assert tt.assess(["docs/STATUS.md"], "RJ-authorized: abcdef1", HEAD).severity == "blocker"


def test_blocker_names_the_line_to_add() -> None:
    message = tt.assess(TRADING, RESERVED, HEAD).message
    assert "`RJ-authorized: abcdef1`" in message


def test_rj_authorized_heads_reads_raw_lines() -> None:
    body = "```\nRJ-authorized: ABCDEF1\n```\n- RJ-authorized: 1234567 later\nRJ-authorized: nope"
    assert tt.rj_authorized_heads(body) == ["abcdef1", "1234567"]


def test_gate_runner_files_are_flagged() -> None:
    runner = [
        ".github/workflows/trading-trigger.yml",
        "scripts/ci/trading_trigger_status.py",
        "tests/unit/scripts/test_trading_trigger_status.py",
        "tests/unit/gpt_trader/agents/test_trading_trigger_authorized.py",
    ]
    assert tt.trading_paths(runner) == sorted(runner)
