"""Expected-identity-bound Robinhood Crypto account observation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from gpt_trader.features.brokerages.accounts import (
    AccountIdentity,
    AccountObservation,
    AccountProvider,
    ObservedBalance,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import (
    RobinhoodCryptoAccount,
    RobinhoodCryptoHolding,
)


class RobinhoodCryptoViolation(ValueError):
    """Raised when provider evidence does not match the accepted account."""


class RobinhoodCryptoObservationClientProtocol(Protocol):
    def list_accounts(self) -> tuple[RobinhoodCryptoAccount, ...]: ...

    def list_holdings(self) -> tuple[RobinhoodCryptoHolding, ...]: ...


class RobinhoodCryptoAccountReader:
    """Read holdings only after exact account identity attestation."""

    def __init__(
        self,
        *,
        client: RobinhoodCryptoObservationClientProtocol,
        expected_account_number: str,
        clock: Callable[[], datetime],
    ) -> None:
        if not expected_account_number:
            raise ValueError("expected_account_number is required")
        self._client = client
        self._expected_account_number = expected_account_number
        self._clock = clock

    def uses_client(self, client: object) -> bool:
        return self._client is client

    def observe_identity(self) -> AccountIdentity:
        account = self._attested_account()
        return self._identity(account)

    def read_account(self) -> AccountObservation:
        account = self._attested_account()
        holdings = self._client.list_holdings()
        balances = tuple(self._balance(holding) for holding in holdings)
        identity = self._identity(account)
        if account.buying_power_currency != "USD":
            raise RobinhoodCryptoViolation("Robinhood Crypto buying-power currency must be USD")
        return AccountObservation(
            identity=identity,
            generated_at=self._clock(),
            balances=balances,
            buying_power={"crypto_usd": account.buying_power},
            warnings=(
                "Provider credential may retain trade authority; application dispatch is GET-only",
            ),
            source_metadata={
                "interface": "crypto-trading-v2",
                "holding_count": str(len(holdings)),
                "buying_power_currency": account.buying_power_currency,
                "api_tradable": str(account.is_api_tradable).lower(),
                "fee_ratio": "unavailable" if account.fee_ratio is None else str(account.fee_ratio),
                "dispatch": "get-only",
            },
        )

    def _attested_account(self) -> RobinhoodCryptoAccount:
        accounts = self._client.list_accounts()
        if len(accounts) != 1:
            raise RobinhoodCryptoViolation(
                "Robinhood Crypto account response must contain exactly the expected account"
            )
        account = accounts[0]
        if account.account_number != self._expected_account_number:
            raise RobinhoodCryptoViolation(
                "Robinhood Crypto account identity does not match expected"
            )
        return account

    def _identity(self, account: RobinhoodCryptoAccount) -> AccountIdentity:
        scope = hashlib.sha256(
            f"crypto-trading-v2\x1f{account.account_number}".encode()
        ).hexdigest()
        return AccountIdentity(
            provider=AccountProvider.ROBINHOOD_CRYPTO,
            account_id=account.account_number,
            interface="crypto-trading-v2",
            account_type=account.account_type,
            status=account.status,
            scope_fingerprint=scope,
        )

    def _balance(self, holding: RobinhoodCryptoHolding) -> ObservedBalance:
        if holding.account_number != self._expected_account_number:
            raise RobinhoodCryptoViolation(
                "Robinhood Crypto holding account does not match expected"
            )
        if not holding.asset_code:
            raise RobinhoodCryptoViolation("Robinhood Crypto holding asset is missing")
        if holding.total_quantity < 0 or holding.available_quantity < 0:
            raise RobinhoodCryptoViolation("Robinhood Crypto holding quantity is negative")
        hold = holding.total_quantity - holding.available_quantity
        if hold < Decimal("0"):
            raise RobinhoodCryptoViolation(
                "Robinhood Crypto available quantity exceeds total quantity"
            )
        return ObservedBalance(
            asset=holding.asset_code,
            total=holding.total_quantity,
            available=holding.available_quantity,
            hold=hold,
        )


__all__ = [
    "RobinhoodCryptoAccountReader",
    "RobinhoodCryptoObservationClientProtocol",
    "RobinhoodCryptoViolation",
]
