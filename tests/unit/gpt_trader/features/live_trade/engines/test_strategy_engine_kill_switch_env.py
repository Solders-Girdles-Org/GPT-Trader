"""RISK_KILL_SWITCH_ENABLED blocks entries in the live engine (#1300).

Builds the risk manager through the live construction path
(RiskValidationContainer) rather than flipping a mock flag, so the test fails
if the documented emergency-stop variable stops reaching the engine gate.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig, BotRiskConfig
from gpt_trader.app.containers.risk_validation import RiskValidationContainer
from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.execution.submission_result import OrderSubmissionStatus
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

KILL_SWITCH_ENV_VAR = "RISK_KILL_SWITCH_ENABLED"


def _live_risk_manager(config: BotConfig):
    return RiskValidationContainer(
        config=config,
        event_store_provider=MagicMock,
    ).risk_manager


@pytest.fixture
def context(mock_broker, application_container, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "1")
    # Strategy kill switch left off: only the environment engages the halt.
    config = BotConfig(
        symbols=["BTC-USD"],
        interval=1,
        risk=BotRiskConfig(position_fraction=Decimal("0.1")),
    )
    return CoordinatorContext(
        config=config,
        container=application_container,
        broker=mock_broker,
        risk_manager=_live_risk_manager(config),
    )


@pytest.mark.asyncio
async def test_env_kill_switch_blocks_entry_order(engine) -> None:
    assert engine.context.risk_manager.config.kill_switch_enabled is True

    result = await engine._validate_and_place_order(
        symbol="ETH-USD",  # no open position: a pure entry
        decision=Decision(Action.BUY, "entry under emergency stop"),
        price=Decimal("3000"),
        equity=Decimal("10000"),
        quantity_override=Decimal("1"),
        reduce_only_requested=False,
    )

    assert result.status == OrderSubmissionStatus.BLOCKED
    assert result.reason == "kill_switch"
    assert result.decision_trace is not None
    assert result.decision_trace.outcomes["kill_switch"]["status"] == "blocked"
    engine._order_submitter.submit_order_with_result.assert_not_called()
    engine.context.broker.place_order.assert_not_called()


def test_env_kill_switch_unset_leaves_live_manager_disengaged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV_VAR, raising=False)
    risk_manager = _live_risk_manager(BotConfig(symbols=["BTC-USD"]))
    assert risk_manager.config.kill_switch_enabled is False
