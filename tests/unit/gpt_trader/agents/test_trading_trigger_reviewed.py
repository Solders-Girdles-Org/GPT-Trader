"""RJ-1120: a file reviewed as holding a live-capable token is flagged by path.

The review covered the content it saw, so any later edit is reviewed again.
"""

from __future__ import annotations

from pathlib import Path

from gpt_trader.agents import trading_trigger as tt

HEAD = "abcdef1234567890"
REPO = Path(tt.__file__).resolve().parents[3]


def test_reviewed_files_are_flagged_even_under_an_exception() -> None:
    # RJ-1120: the review covered the content it saw, so any edit is flagged.
    assert tt.trading_paths(list(tt.REVIEWED_LIVE_TOKEN)) == sorted(tt.REVIEWED_LIVE_TOKEN)
    under_exception = "src/gpt_trader/backtesting/data/manager.py"
    assert under_exception in tt.REVIEWED_LIVE_TOKEN
    assert tt.trading_paths(["src/gpt_trader/backtesting/data/other.py"]) == []
    assert tt.trading_paths([under_exception.upper()]) == [under_exception.upper()]


def test_turning_a_reviewed_file_live_is_flagged(tmp_path: Path) -> None:
    # RJ-1120 example: candle_capture.py builds its client with auth=None. A PR
    # that gives it a real key and declares Reserved: none must be blocked
    # until a clean cross-family review of its head confirms that.
    path = "src/gpt_trader/features/recorder/candle_capture.py"
    live = (REPO / path).read_text(encoding="utf-8").replace("auth=None", "auth=SimpleAuth(k, s)")
    assert live != (REPO / path).read_text(encoding="utf-8")
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(live)
    result = tt.assess([path], "Reserved: none", HEAD)
    assert result.severity == "blocker"
    assert result.paths == (path,)
    # Flagged, so the backstop scan does not need to pass judgment on it.
    assert tt.unflagged_live_capable(tmp_path, [path]) == []
