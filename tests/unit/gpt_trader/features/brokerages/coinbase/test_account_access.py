from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.features.brokerages.accounts import AccountProvider
from gpt_trader.features.brokerages.coinbase.account_access import (
    CoinbaseAccountReader,
    CoinbaseAccountViolation,
)
from gpt_trader.features.brokerages.coinbase.errors import PermissionDeniedError


class StubCoinbaseObservationClient:
    def __init__(self) -> None:
        self.permissions: dict[str, Any] = {
            "can_view": True,
            "can_trade": False,
            "can_transfer": False,
            "can_receive": False,
            "portfolio_uuid": "portfolio-1",
            "portfolio_type": "DEFAULT",
        }
        self.accounts: dict[str, Any] = {
            "accounts": [
                {
                    "uuid": "usd-account",
                    "currency": "USD",
                    "available_balance": {"value": "100.25"},
                    "hold": {"value": "10.75"},
                },
                {
                    "uuid": "btc-account",
                    "currency": "BTC",
                    "available_balance": {"value": "0.5"},
                    "hold": {"value": "0.1"},
                },
            ]
        }
        self.cfm_positions: dict[str, Any] = {
            "positions": [
                {
                    "product_id": "BIT-28AUG26-CDE",
                    "number_of_contracts": "2",
                    "side": "LONG",
                    "avg_entry_price": "65000",
                }
            ]
        }
        self.cfm_balance: dict[str, Any] = {
            "balance_summary": {
                "cfm_usd_balance": {"value": "750.00"},
                "available_margin": {"value": "700.00"},
                "total_open_orders_hold_amount": {"value": "50.00"},
                "futures_buying_power": {"value": "1400.00"},
            }
        }
        self.products: dict[str, dict[str, Any]] = {
            "BIT-28AUG26-CDE": {
                "product_id": "BIT-28AUG26-CDE",
                "future_product_details": {"non_crypto": False},
            }
        }
        self.cfm_error: Exception | None = None
        self.calls: list[str] = []

    def get_key_permissions(self) -> dict[str, Any]:
        self.calls.append("get_key_permissions")
        return self.permissions

    def list_all_accounts(self) -> dict[str, Any]:
        self.calls.append("list_all_accounts")
        return self.accounts

    def list_cfm_positions(self) -> dict[str, Any]:
        self.calls.append("list_cfm_positions")
        if self.cfm_error is not None:
            raise self.cfm_error
        return self.cfm_positions

    def get_cfm_balance_summary(self) -> dict[str, Any]:
        self.calls.append("get_cfm_balance_summary")
        if self.cfm_error is not None:
            raise self.cfm_error
        return self.cfm_balance

    def get_product(self, product_id: str) -> dict[str, Any]:
        self.calls.append(f"get_product:{product_id}")
        return self.products[product_id]


def _reader(client: StubCoinbaseObservationClient) -> CoinbaseAccountReader:
    return CoinbaseAccountReader(
        client=client,
        expected_portfolio_uuid="portfolio-1",
        expected_account_uuids=frozenset({"usd-account", "btc-account"}),
        include_cfm=True,
        clock=lambda: datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
    )


def test_observe_identity_requires_view_only_expected_portfolio() -> None:
    client = StubCoinbaseObservationClient()

    identity = _reader(client).observe_identity()

    assert identity.provider is AccountProvider.COINBASE
    assert identity.account_id == "portfolio-1"
    assert identity.portfolio_id == "portfolio-1"
    assert identity.interface == "advanced-trade-v3"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("can_view", False, "view permission"),
        ("can_trade", True, "trade permission"),
        ("can_transfer", True, "transfer permission"),
        ("can_receive", True, "receive permission"),
        ("portfolio_uuid", "portfolio-2", "portfolio identity"),
    ],
)
def test_observe_identity_rejects_unsafe_permissions_or_wrong_portfolio(
    field: str,
    value: object,
    message: str,
) -> None:
    client = StubCoinbaseObservationClient()
    client.permissions[field] = value

    with pytest.raises(CoinbaseAccountViolation, match=message):
        _reader(client).observe_identity()


def test_read_account_maps_balances_and_visible_cfm_positions() -> None:
    observation = _reader(StubCoinbaseObservationClient()).read_account()

    assert observation.generated_at == datetime(2026, 7, 12, 15, 30, tzinfo=UTC)
    assert {balance.asset: balance.total for balance in observation.balances} == {
        "USD": Decimal("111.00"),
        "BTC": Decimal("0.6"),
        "CFM_USD": Decimal("750.00"),
    }
    assert observation.buying_power == {"cfm_futures": Decimal("1400.00")}
    assert [
        (position.instrument, position.asset_class.value, position.product_type.value)
        for position in observation.positions
    ] == [("BIT-28AUG26-CDE", "crypto", "futures")]
    assert observation.positions[0].quantity == Decimal("2")
    assert observation.positions[0].average_cost == Decimal("65000")
    assert observation.warnings == ()


def test_read_account_preserves_short_cfm_direction_with_signed_quantity() -> None:
    client = StubCoinbaseObservationClient()
    client.cfm_positions = {
        "positions": [
            {
                "product_id": "BIT-28AUG26-CDE",
                "number_of_contracts": "2",
                "side": "SHORT",
            }
        ]
    }

    observation = _reader(client).read_account()

    cfm_position = observation.positions[-1]
    assert cfm_position.product_type.value == "futures"
    assert cfm_position.quantity == Decimal("-2")


def test_read_account_rejects_contract_count_without_cfm_side() -> None:
    client = StubCoinbaseObservationClient()
    client.cfm_positions = {
        "positions": [
            {
                "product_id": "BIT-28AUG26-CDE",
                "number_of_contracts": "2",
            }
        ]
    }

    with pytest.raises(CoinbaseAccountViolation, match="position side"):
        _reader(client).read_account()


def test_observe_identity_refuses_unexpected_account_uuid_set() -> None:
    client = StubCoinbaseObservationClient()
    client.accounts["accounts"].append(
        {
            "uuid": "unexpected-account",
            "currency": "ETH",
            "available_balance": {"value": "1"},
            "hold": {"value": "0"},
        }
    )

    with pytest.raises(CoinbaseAccountViolation, match="account identities"):
        _reader(client).observe_identity()


def test_read_account_refuses_unexpected_account_uuid_set() -> None:
    client = StubCoinbaseObservationClient()
    client.accounts["accounts"].append(
        {
            "uuid": "unexpected-account",
            "currency": "ETH",
            "available_balance": {"value": "1"},
            "hold": {"value": "0"},
        }
    )

    with pytest.raises(CoinbaseAccountViolation, match="account identities"):
        _reader(client).read_account()


def test_read_account_reports_enabled_cfm_permission_gap() -> None:
    client = StubCoinbaseObservationClient()
    client.cfm_error = PermissionDeniedError("CFM unavailable")

    observation = _reader(client).read_account()

    assert observation.positions == ()
    assert observation.warnings == ("CFM account data unavailable: PermissionDeniedError",)


def test_read_account_skips_cfm_when_not_enabled() -> None:
    client = StubCoinbaseObservationClient()
    client.cfm_error = AssertionError("CFM should not be called")
    reader = CoinbaseAccountReader(
        client=client,
        expected_portfolio_uuid="portfolio-1",
        expected_account_uuids=frozenset({"usd-account", "btc-account"}),
        include_cfm=False,
        clock=lambda: datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
    )

    observation = reader.read_account()

    assert observation.positions == ()
    assert "get_cfm_balance_summary" not in client.calls
    assert "list_cfm_positions" not in client.calls


def test_read_account_rejects_non_crypto_futures_until_schema_supports_them() -> None:
    client = StubCoinbaseObservationClient()
    client.products["BIT-28AUG26-CDE"]["future_product_details"]["non_crypto"] = True

    with pytest.raises(CoinbaseAccountViolation, match="non-crypto futures"):
        _reader(client).read_account()


def test_read_account_requires_complete_cfm_balance_evidence() -> None:
    client = StubCoinbaseObservationClient()
    del client.cfm_balance["balance_summary"]["futures_buying_power"]

    with pytest.raises(CoinbaseAccountViolation, match="futures buying power"):
        _reader(client).read_account()
