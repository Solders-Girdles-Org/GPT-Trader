"""Identity-bound account and portfolio observation for Robinhood Agentic."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from gpt_trader.features.brokerages.accounts import (
    AccountIdentity,
    AccountObservation,
    AccountProvider,
    ObservedBalance,
)
from gpt_trader.features.brokerages.robinhood.agentic.errors import (
    RobinhoodAgenticViolation,
)
from gpt_trader.features.brokerages.robinhood.agentic.transport import (
    RobinhoodAgenticGatewayProtocol,
)


def _decimal(value: Any, field_name: str) -> Decimal:
    if value is None or value == "":
        raise RobinhoodAgenticViolation(f"Robinhood Agentic {field_name} is missing")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RobinhoodAgenticViolation(f"Robinhood Agentic {field_name} is malformed") from None
    if not result.is_finite():
        raise RobinhoodAgenticViolation(f"Robinhood Agentic {field_name} must be finite")
    return result


class RobinhoodAgenticAccountReader:
    def __init__(
        self,
        *,
        gateway: RobinhoodAgenticGatewayProtocol,
        expected_account_number: str,
        clock: Callable[[], datetime],
    ) -> None:
        if not expected_account_number.strip():
            raise ValueError("expected_account_number is required")
        self._gateway = gateway
        self._expected_account_number = expected_account_number.strip()
        self._clock = clock

    def uses_gateway(self, gateway: object) -> bool:
        return self._gateway is gateway

    async def observe_identity(self) -> AccountIdentity:
        payload = await self._gateway.get_accounts()
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RobinhoodAgenticViolation("Robinhood Agentic accounts response is malformed")
        rows = data.get("accounts")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RobinhoodAgenticViolation("Robinhood Agentic accounts response is malformed")
        account_numbers = [str(row.get("account_number") or "") for row in rows]
        if "" in account_numbers or len(set(account_numbers)) != len(account_numbers):
            raise RobinhoodAgenticViolation("Robinhood Agentic account identity is malformed")
        accessible = [row for row in rows if row.get("agentic_allowed") is True]
        if len(accessible) != 1:
            raise RobinhoodAgenticViolation(
                "Robinhood Agentic accessible account set does not match expected"
            )
        row = accessible[0]
        if row["account_number"] != self._expected_account_number:
            raise RobinhoodAgenticViolation(
                "Robinhood Agentic account identity does not match expected"
            )
        if (
            row.get("state") != "active"
            or row.get("deactivated") is not False
            or row.get("permanently_deactivated") is not False
        ):
            raise RobinhoodAgenticViolation("Robinhood Agentic account is not active")
        scope = "\x1f".join(
            (
                *sorted(account_numbers),
                self._gateway.schema_set_fingerprint,
                self._gateway.tool_name_fingerprint,
            )
        )
        return AccountIdentity(
            provider=AccountProvider.ROBINHOOD_AGENTIC,
            account_id=self._expected_account_number,
            interface="agentic-mcp",
            account_type=str(row.get("brokerage_account_type") or row.get("type") or ""),
            status=str(row.get("state") or ""),
            scope_fingerprint=hashlib.sha256(scope.encode()).hexdigest(),
        )

    async def read_account(self) -> AccountObservation:
        identity = await self.observe_identity()
        payload = await self._gateway.get_portfolio(self._expected_account_number)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RobinhoodAgenticViolation("Robinhood Agentic portfolio response is malformed")
        currency = str(data.get("currency") or "")
        if not currency:
            raise RobinhoodAgenticViolation("Robinhood Agentic portfolio currency is missing")
        total = _decimal(data.get("total_value"), "total value")
        cash = _decimal(data.get("cash"), "cash")
        buying_power_data = data.get("buying_power")
        if not isinstance(buying_power_data, dict):
            raise RobinhoodAgenticViolation("Robinhood Agentic buying power is malformed")
        display_currency = str(buying_power_data.get("display_currency") or "")
        if display_currency != currency:
            raise RobinhoodAgenticViolation(
                "Robinhood Agentic buying-power currency does not match portfolio"
            )
        buying_power = {
            "buying_power": _decimal(buying_power_data.get("buying_power"), "buying power"),
            "unleveraged_buying_power": _decimal(
                buying_power_data.get("unleveraged_buying_power"),
                "unleveraged buying power",
            ),
        }
        source_metadata = {
            "interface": "agentic-mcp",
            "schema_set_fingerprint": self._gateway.schema_set_fingerprint,
        }
        for field_name in (
            "equity_value",
            "options_value",
            "futures_value",
            "event_contracts_value",
            "crypto_value",
            "pending_deposits",
            "mutual_funds_value",
            "fixed_income_value",
        ):
            source_metadata[field_name] = str(_decimal(data.get(field_name), field_name))
        return AccountObservation(
            identity=identity,
            generated_at=self._clock(),
            balances=(ObservedBalance(asset=currency, total=total, available=cash),),
            buying_power=buying_power,
            source_metadata=source_metadata,
        )


__all__ = ["RobinhoodAgenticAccountReader"]
