from __future__ import annotations

from typing import Any, cast

import pytest

from gpt_trader.features.brokerages.coinbase.account_access import CoinbaseAccountReader
from gpt_trader.features.brokerages.coinbase.preview_access import CoinbasePreviewProvider
from gpt_trader.features.brokerages.coinbase.read_preview_access import CoinbaseReadPreviewAccess


class Reader:
    def observe_identity(self) -> Any:
        return SimpleNamespace(fingerprint="identity-fingerprint")


class SimpleNamespace:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


def test_access_exposes_typed_adapters_without_raw_client() -> None:
    closed = False

    def close_client() -> None:
        nonlocal closed
        closed = True

    access = CoinbaseReadPreviewAccess(
        reader=cast(CoinbaseAccountReader, cast(Any, Reader())),
        preview_provider=cast(CoinbasePreviewProvider, cast(Any, object())),
        _close_client=close_client,
        _list_orders=lambda: {"orders": []},
    )

    assert hasattr(access, "client") is False
    access.close()
    assert closed is True


def test_order_history_is_identity_bound_and_immutable() -> None:
    access = CoinbaseReadPreviewAccess(
        reader=cast(CoinbaseAccountReader, cast(Any, Reader())),
        preview_provider=cast(CoinbasePreviewProvider, cast(Any, object())),
        _close_client=lambda: None,
        _list_orders=lambda: {
            "orders": [
                {
                    "order_id": "order-1",
                    "product_id": "BTC-USD",
                    "side": "BUY",
                    "status": "OPEN",
                    "filled_size": "0",
                    "filled_value": "0",
                    "outstanding_hold_amount": "10",
                    "order_configuration": {"limit_limit_gtc": {"limit_price": "10"}},
                }
            ]
        },
    )

    evidence = access.read_order_history()

    assert evidence.identity_fingerprint == "identity-fingerprint"
    assert evidence.orders[0].order_id == "order-1"
    with pytest.raises(AttributeError):
        evidence.orders[0].status = "FILLED"  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"orders": ["not-an-object"]},
        {"orders": [{"order_id": ""}]},
        {"orders": [{"order_id": "order-1", "order_configuration": []}]},
    ],
)
def test_order_history_rejects_malformed_payload(payload: dict[str, Any]) -> None:
    access = CoinbaseReadPreviewAccess(
        reader=cast(CoinbaseAccountReader, cast(Any, Reader())),
        preview_provider=cast(CoinbasePreviewProvider, cast(Any, object())),
        _close_client=lambda: None,
        _list_orders=lambda: payload,
    )

    with pytest.raises(ValueError, match="malformed|missing"):
        access.read_order_history()
