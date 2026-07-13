from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.features.brokerages.accounts import AccountProvider
from gpt_trader.features.brokerages.robinhood.crypto.account_access import (
    RobinhoodCryptoAccountReader,
    RobinhoodCryptoViolation,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import (
    RobinhoodCryptoAccount,
    RobinhoodCryptoHolding,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)
ACCOUNT_NUMBER = "account-123"


def account(number: str = ACCOUNT_NUMBER) -> RobinhoodCryptoAccount:
    return RobinhoodCryptoAccount(
        account_number=number,
        status="active",
        buying_power=Decimal("100"),
        buying_power_currency="USD",
        account_type="individual",
        is_api_tradable=True,
        fee_ratio=Decimal("0.0065"),
        thirty_day_volume=Decimal("0"),
        next_fee_tier_ratio=Decimal("0.0055"),
        next_fee_tier_threshold=Decimal("10000"),
    )


def holding(
    number: str = ACCOUNT_NUMBER,
    *,
    total: str = "2",
    available: str = "1.5",
) -> RobinhoodCryptoHolding:
    return RobinhoodCryptoHolding(
        account_number=number,
        asset_code="BTC",
        total_quantity=Decimal(total),
        available_quantity=Decimal(available),
    )


class Client:
    def __init__(
        self,
        accounts: tuple[RobinhoodCryptoAccount, ...],
        holdings: tuple[RobinhoodCryptoHolding, ...] = (),
    ) -> None:
        self.accounts = accounts
        self.holdings = holdings

    def list_accounts(self) -> tuple[RobinhoodCryptoAccount, ...]:
        return self.accounts

    def list_holdings(self) -> tuple[RobinhoodCryptoHolding, ...]:
        return self.holdings


def reader(client: Client) -> RobinhoodCryptoAccountReader:
    return RobinhoodCryptoAccountReader(
        client=client,
        expected_account_number=ACCOUNT_NUMBER,
        clock=lambda: NOW,
    )


def test_observes_exact_identity_and_holding_balances() -> None:
    result = reader(Client((account(),), (holding(),))).read_account()

    assert result.identity.provider is AccountProvider.ROBINHOOD_CRYPTO
    assert result.identity.account_id == ACCOUNT_NUMBER
    assert result.generated_at == NOW
    assert result.buying_power == {"crypto_usd": Decimal("100")}
    assert result.balances[0].total == Decimal("2")
    assert result.balances[0].available == Decimal("1.5")
    assert result.balances[0].hold == Decimal("0.5")
    assert result.source_metadata["dispatch"] == "get-only"
    assert "trade authority" in result.warnings[0]


@pytest.mark.parametrize(
    "accounts",
    [
        (),
        (account("wrong"),),
        (account(), account()),
        (account(), account("wrong")),
    ],
)
def test_rejects_non_exact_account_set(
    accounts: tuple[RobinhoodCryptoAccount, ...],
) -> None:
    with pytest.raises(RobinhoodCryptoViolation, match="account"):
        reader(Client(accounts)).observe_identity()


def test_rejects_holding_for_other_account() -> None:
    with pytest.raises(RobinhoodCryptoViolation, match="holding account"):
        reader(Client((account(),), (holding("wrong"),))).read_account()


@pytest.mark.parametrize(
    ("total", "available"),
    [("-1", "0"), ("1", "-1"), ("1", "2")],
)
def test_rejects_invalid_holding_quantities(total: str, available: str) -> None:
    with pytest.raises(RobinhoodCryptoViolation, match="quantity"):
        reader(Client((account(),), (holding(total=total, available=available),))).read_account()


def test_identity_fingerprint_is_stable_and_expected_account_bound() -> None:
    first = reader(Client((account(),))).observe_identity()
    second = reader(Client((account(),))).observe_identity()

    assert first.fingerprint == second.fingerprint
    assert first.scope_fingerprint


def test_rejects_non_usd_buying_power_currency() -> None:
    other_currency = RobinhoodCryptoAccount(
        account_number=ACCOUNT_NUMBER,
        status="active",
        buying_power=Decimal("100"),
        buying_power_currency="EUR",
        account_type="individual",
        is_api_tradable=True,
        fee_ratio=Decimal("0.0065"),
        thirty_day_volume=Decimal("0"),
        next_fee_tier_ratio=Decimal("0.0055"),
        next_fee_tier_threshold=Decimal("10000"),
    )

    with pytest.raises(RobinhoodCryptoViolation, match="currency"):
        reader(Client((other_currency,))).read_account()
