from __future__ import annotations

from pathlib import Path

from gpt_trader.features.trade_ideas.service import TradeIdeaService
from tests.unit.gpt_trader.features.trade_ideas.conftest import attest_account_equity

__all__ = ["attest_account_equity", "attest_ideas_root"]


def attest_ideas_root(root: Path) -> None:
    """Attest operator equity at an ideas root so approvals can verify notional caps."""
    attest_account_equity(TradeIdeaService(root))
