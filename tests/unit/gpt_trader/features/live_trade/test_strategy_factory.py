from __future__ import annotations

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.features.live_trade.factory import create_strategy


def test_create_strategy_rejects_retired_ensemble_type() -> None:
    config = BotConfig(symbols=["BTC-USD"])
    config.strategy_type = "ensemble"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="ensemble"):
        create_strategy(config)
