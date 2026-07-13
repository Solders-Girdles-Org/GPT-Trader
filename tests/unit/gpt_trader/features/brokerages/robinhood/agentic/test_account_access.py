from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.features.brokerages.accounts import AccountProvider
from gpt_trader.features.brokerages.robinhood.agentic.account_access import (
    RobinhoodAgenticAccountReader,
)
from gpt_trader.features.brokerages.robinhood.agentic.errors import RobinhoodAgenticViolation

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def reader(gateway: Any) -> RobinhoodAgenticAccountReader:
    return RobinhoodAgenticAccountReader(
        gateway=gateway,
        expected_account_number="RH-EXPECTED",
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_observe_account_binds_exact_accessible_identity(gateway: Any) -> None:
    observation = await reader(gateway).read_account()

    assert observation.identity.provider is AccountProvider.ROBINHOOD_AGENTIC
    assert observation.identity.account_id == "RH-EXPECTED"
    assert observation.identity.interface == "agentic-mcp"
    assert observation.balances[0].total == Decimal("100.00")
    assert observation.balances[0].available == Decimal("100.00")
    assert observation.buying_power["buying_power"] == Decimal("100.00")
    assert gateway.calls == [
        ("get_accounts", {}),
        ("get_portfolio", "RH-EXPECTED"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["wrong-account", "two-accessible", "inactive", "duplicate", "malformed"],
)
async def test_identity_mismatch_fails_before_portfolio(gateway: Any, mutation: str) -> None:
    row = gateway.accounts_payload["data"]["accounts"][0]
    if mutation == "wrong-account":
        row["account_number"] = "OTHER"
    elif mutation == "two-accessible":
        gateway.accounts_payload["data"]["accounts"].append({**row, "account_number": "OTHER"})
    elif mutation == "inactive":
        row["state"] = "restricted"
    elif mutation == "duplicate":
        gateway.accounts_payload["data"]["accounts"].append(dict(row))
    else:
        gateway.accounts_payload["data"]["accounts"] = [None]

    with pytest.raises(RobinhoodAgenticViolation):
        await reader(gateway).read_account()
    assert all(name != "get_portfolio" for name, _ in gateway.calls)


@pytest.mark.asyncio
async def test_malformed_portfolio_decimal_fails_closed(gateway: Any) -> None:
    gateway.portfolio_payload["data"]["total_value"] = "NaN"
    with pytest.raises(RobinhoodAgenticViolation, match="finite"):
        await reader(gateway).read_account()
