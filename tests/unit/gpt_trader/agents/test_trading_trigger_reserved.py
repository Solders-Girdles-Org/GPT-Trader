"""Reserved lines the plain parse skips still block (RJ-1488)."""

from __future__ import annotations

import pytest

from gpt_trader.agents import trading_trigger as tt

HEAD = "abcdef1234567890"
LINK = "https://github.com/o/r/pull/1#pullrequestreview-1"

LIVE_BOT = ["src/gpt_trader/features/live_trade/bot.py"]
CLEAN = f"Review: {LINK} (Claude) at abcdef1: clean; no blocking findings"
EFFECT = "e - places live orders"


@pytest.mark.parametrize(
    "variant",
    [
        f"- Reserved: {EFFECT}",
        f"1. Reserved: {EFFECT}",
        f"2) Reserved: {EFFECT}",
        f"+ Reserved: {EFFECT}",
        f"> Reserved: {EFFECT}",
        f"> - **Reserved:** {EFFECT}",
        f"| Reserved: {EFFECT} |",
        f"| Effect | Reserved: {EFFECT} |",
        f"    Reserved: {EFFECT}",
        f" Reserved: {EFFECT}",
        f"Reserved： {EFFECT}",
        f"Reserved∶ {EFFECT}",
        f"Re​served: {EFFECT}",
        f"- [ ] Reserved: {EFFECT}",
        f"## Reserved: {EFFECT}",
    ],
)
def test_reserved_effect_in_any_container_beats_a_leftover_none(variant: str) -> None:
    # RJ-1488: a skipped container used to leave only `none`, which passed.
    body = f"Reserved: none\n{variant}\n{CLEAN}"
    assert tt.assess(LIVE_BOT, body, HEAD).severity == "blocker"
    assert tt.assess(["docs/STATUS.md"], body, HEAD).severity == "blocker"


@pytest.mark.parametrize("variant", ["> Reserved: none", "1. Reserved: none", "| Reserved: none |"])
def test_container_none_alone_is_still_no_declaration(variant: str) -> None:
    # The wider reading only adds effects; it never supplies the declaration.
    assert tt.reserved_values(variant) == []
    assert tt.assess(["docs/STATUS.md"], variant, HEAD).severity == "blocker"
    body = f"Reserved: none\n{variant}\n{CLEAN}"
    assert tt.assess(LIVE_BOT, body, HEAD).severity == "info"


@pytest.mark.parametrize(
    "line",
    [
        "Not reserved: the cleanup keeps every gate.",
        "The unreserved: path is unchanged.",
        "| Reserved | Effect |",
        "See the Reserved line below.",
    ],
)
def test_prose_mentions_are_not_declarations(line: str) -> None:
    assert tt.reserved_values(f"Reserved: none\n{line}") == ["none"]


@pytest.mark.parametrize("unit", ["[ ]", "[x]", "1.", "- ", "_"])
def test_wide_reading_does_not_backtrack_on_long_prefixes(unit: str) -> None:
    # Overlapping prefix alternatives once took seconds at 23 checkboxes.
    import time

    start = time.monotonic()
    assert tt.reserved_values("Reserved: none\n" + unit * 2000 + "Summary") == ["none"]
    assert tt.reserved_values(unit * 2000 + "Reserved: e - x") == ["e - x"]
    assert time.monotonic() - start < 1.0
