from __future__ import annotations

from typing import Any

import pytest


class FakeGateway:
    schema_set_fingerprint = "a" * 64
    tool_name_fingerprint = "b" * 64

    def __init__(self) -> None:
        self.accounts_payload: dict[str, Any] = {
            "data": {
                "accounts": [
                    {
                        "account_number": "RH-EXPECTED",
                        "rhs_account_number": "1234",
                        "type": "cash",
                        "brokerage_account_type": "individual",
                        "is_default": True,
                        "agentic_allowed": True,
                        "option_level": "option_level_2",
                        "state": "active",
                        "deactivated": False,
                        "permanently_deactivated": False,
                    }
                ]
            },
            "guide": "",
        }
        self.portfolio_payload: dict[str, Any] = {
            "data": {
                "total_value": "100.00",
                "equity_value": "0",
                "options_value": "0",
                "futures_value": "0",
                "event_contracts_value": "0",
                "crypto_value": "0",
                "cash": "100.00",
                "pending_deposits": "0",
                "mutual_funds_value": "0",
                "fixed_income_value": "0",
                "currency": "USD",
                "buying_power": {
                    "buying_power": "100.00",
                    "unleveraged_buying_power": "100.00",
                    "display_currency": "USD",
                },
            },
            "guide": "",
        }
        self.equity_payload: dict[str, Any] = {}
        self.option_payload: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    async def get_accounts(self) -> dict[str, Any]:
        self.calls.append(("get_accounts", {}))
        return self.accounts_payload

    async def get_portfolio(self, account_number: str) -> dict[str, Any]:
        self.calls.append(("get_portfolio", account_number))
        return self.portfolio_payload

    async def review_equity_order(self, arguments: Any) -> dict[str, Any]:
        self.calls.append(("review_equity_order", dict(arguments)))
        return self.equity_payload

    async def review_option_order(self, arguments: Any) -> dict[str, Any]:
        self.calls.append(("review_option_order", dict(arguments)))
        return self.option_payload

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()
