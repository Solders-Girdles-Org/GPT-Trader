from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from gpt_trader.features.brokerages.robinhood.crypto.client import RobinhoodCryptoClient

PRIVATE_BYTES = bytes(range(32))
PRIVATE_KEY = base64.b64encode(PRIVATE_BYTES).decode()
ACCOUNT = "account-123"


class Response:
    def __init__(self, url: str, payload: Any, status_code: int = 200) -> None:
        self.url = url
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Session:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Mapping[str, str], float, bool]] = []
        self.closed = False

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> Response:
        self.calls.append((url, headers, timeout, allow_redirects))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def client(session: Session) -> RobinhoodCryptoClient:
    return RobinhoodCryptoClient(
        api_key="api-key",
        private_key=PRIVATE_KEY,
        expected_account_number=ACCOUNT,
        session=session,
        timestamp=lambda: 1_700_000_000,
    )


def page(results: list[dict[str, Any]], *, next_url: str | None = None) -> dict[str, Any]:
    return {"next": next_url, "previous": None, "results": results}


def account_row(account_number: str = ACCOUNT) -> dict[str, Any]:
    return {
        "account_number": account_number,
        "status": "active",
        "buying_power": "100.25",
        "buying_power_currency": "USD",
        "account_type": "individual",
        "is_api_tradable": True,
        "fee_tier_status": {
            "fee_ratio": "0.0065",
            "thirty_day_volume": "0",
            "next_fee_tier_ratio": "0.0055",
            "next_fee_tier_threshold": "10000",
        },
    }


def holding_row(account_number: str = ACCOUNT) -> dict[str, Any]:
    return {
        "account_number": account_number,
        "asset_code": "BTC",
        "total_quantity": "1.25",
        "quantity_available_for_trading": "1.20",
    }


def order_row(account_number: str = ACCOUNT) -> dict[str, Any]:
    return {
        "id": "order-1",
        "account_number": account_number,
        "symbol": "BTC-USD",
        "client_order_id": "client-1",
        "side": "buy",
        "executions": [],
        "type": "limit",
        "state": "open",
        "average_price": None,
        "filled_asset_quantity": "0",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "limit_order_config": {"asset_quantity": "1", "limit_price": "10"},
        "fee_charged": "0",
        "estimated_fee_remaining": "0.1",
    }


def pair_row(symbol: str = "BTC-USD") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "asset_code": symbol.split("-")[0],
        "quote_code": "USD",
        "asset_increment": "0.00000001",
        "quote_increment": "0.01",
        "max_order_size": "100",
        "min_order_amount": "1",
        "status": "tradable",
        "is_api_tradable": True,
    }


def estimate_row() -> dict[str, Any]:
    return {
        "symbol": "BTC-USD",
        "side": "ask",
        "quantity": "0.1",
        "timestamp": "2026-07-13T00:00:00Z",
        "bid": "99",
        "ask": "101",
        "fee_ratio": "0.01",
        "est_fee": "0.10",
        "est_total_cost": "10.20",
        "est_total_credit": "9.80",
    }
