from __future__ import annotations

from pathlib import Path

import pytest

_TRIGGER = Path(__file__).resolve().parents[3] / "src/gpt_trader/agents/trading_trigger.py"


@pytest.fixture(autouse=True)
def _base_trigger_from_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the base branch's trigger from this checkout instead of GitHub."""
    monkeypatch.setattr(
        "scripts.agents.pr_readiness.fetch_base_trigger_source",
        lambda repo, base_ref_name: _TRIGGER.read_text(),
    )
