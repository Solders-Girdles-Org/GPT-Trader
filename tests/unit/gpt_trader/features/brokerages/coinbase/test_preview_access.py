from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.features.brokerages.accounts import PreviewKind, PreviewRequest
from gpt_trader.features.brokerages.coinbase.account_access import CoinbaseAccountReader
from gpt_trader.features.brokerages.coinbase.preview_access import (
    CoinbasePreviewProvider,
    CoinbasePreviewViolation,
    build_coinbase_preview_payload,
)


class StubCoinbaseIdentityClient:
    def get_key_permissions(self) -> dict[str, Any]:
        return {
            "can_view": True,
            "can_trade": False,
            "can_transfer": False,
            "can_receive": False,
            "portfolio_uuid": "portfolio-1",
            "portfolio_type": "DEFAULT",
        }

    def list_all_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {"uuid": "usd-account", "currency": "USD"},
                {"uuid": "btc-account", "currency": "BTC"},
            ]
        }

    def list_cfm_positions(self) -> dict[str, Any]:
        return {"positions": []}


class StubCoinbasePreviewClient(StubCoinbaseIdentityClient):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response: dict[str, Any] = {
            "order_total": "1001.25",
            "commission_total": "1.25",
            "best_bid": "99900",
            "best_ask": "100000",
            "est_average_filled_price": "100000",
            "warning": ["price moved"],
            "errs": [],
            "preview_id": "must-not-escape",
        }

    def preview_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return self.response

    def place_order(self, payload: dict[str, Any]) -> None:
        raise AssertionError("preview adapter must never place an order")


def _reader(
    client: StubCoinbaseIdentityClient | None = None,
) -> CoinbaseAccountReader:
    return CoinbaseAccountReader(
        client=client or StubCoinbaseIdentityClient(),
        expected_portfolio_uuid="portfolio-1",
        expected_account_uuids=frozenset({"usd-account", "btc-account"}),
        include_cfm=False,
        clock=lambda: datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
    )


def test_build_coinbase_preview_payload_supports_market_and_limit() -> None:
    market = build_coinbase_preview_payload(
        PreviewRequest(
            instrument="BTC-USD",
            side="buy",
            quantity=Decimal("0.01"),
            order_type="market",
        )
    )
    limit = build_coinbase_preview_payload(
        PreviewRequest(
            instrument="BTC-USD",
            side="sell",
            quantity=Decimal("0.02"),
            order_type="limit",
            limit_price=Decimal("110000"),
        )
    )

    assert market == {
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_configuration": {"market_market_ioc": {"base_size": "0.01"}},
    }
    assert limit == {
        "product_id": "BTC-USD",
        "side": "SELL",
        "order_configuration": {
            "limit_limit_gtc": {
                "base_size": "0.02",
                "limit_price": "110000",
                "post_only": False,
            }
        },
    }


def test_preview_uses_validated_payload_and_returns_identity_bound_result() -> None:
    client = StubCoinbasePreviewClient()
    provider = CoinbasePreviewProvider(
        client=client,
        account_reader=_reader(client),
        clock=lambda: datetime(2026, 7, 12, 15, 31, tzinfo=UTC),
    )
    request = PreviewRequest(
        instrument="BTC-USD",
        side="buy",
        quantity=Decimal("0.01"),
        order_type="market",
    )

    result = provider.preview(request)

    assert client.calls == [build_coinbase_preview_payload(request)]
    assert result.kind is PreviewKind.PROVIDER_SIMULATION
    assert result.identity_fingerprint == _reader(client).observe_identity().fingerprint
    assert result.estimated_price == Decimal("100000")
    assert result.estimated_fee == Decimal("1.25")
    assert result.estimated_total == Decimal("1001.25")
    assert result.warnings == ("price moved",)
    assert result.errors == ()
    assert "preview_id" not in result.to_dict()


def test_preview_preserves_structured_provider_errors() -> None:
    client = StubCoinbasePreviewClient()
    client.response = {
        "order_total": "0",
        "commission_total": "0",
        "best_bid": "99900",
        "best_ask": "100000",
        "warning": "size rounded",
        "errs": ["INSUFFICIENT_FUND", {"message": "product disabled"}],
    }
    provider = CoinbasePreviewProvider(
        client=client,
        account_reader=_reader(client),
        clock=lambda: datetime(2026, 7, 12, 15, 31, tzinfo=UTC),
    )

    result = provider.preview(
        PreviewRequest(
            instrument="BTC-USD",
            side="sell",
            quantity=Decimal("0.01"),
            order_type="market",
        )
    )

    assert result.estimated_price == Decimal("99900")
    assert result.warnings == ("size rounded",)
    assert result.errors == ("INSUFFICIENT_FUND", "product disabled")


def test_preview_rejects_malformed_response() -> None:
    client = StubCoinbasePreviewClient()
    client.response = {"order_total": "not-a-number"}
    provider = CoinbasePreviewProvider(
        client=client,
        account_reader=_reader(client),
        clock=lambda: datetime(2026, 7, 12, 15, 31, tzinfo=UTC),
    )

    with pytest.raises(CoinbasePreviewViolation, match="decimal"):
        provider.preview(
            PreviewRequest(
                instrument="BTC-USD",
                side="buy",
                quantity=Decimal("0.01"),
                order_type="market",
            )
        )
