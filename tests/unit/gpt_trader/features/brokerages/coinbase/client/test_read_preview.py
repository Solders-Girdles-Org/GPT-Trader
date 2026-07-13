from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from gpt_trader.features.brokerages.coinbase.client.read_preview import (
    CoinbaseReadPreviewClient,
)
from gpt_trader.features.brokerages.coinbase.errors import (
    InvalidRequestError,
    PermissionDeniedError,
)


def _client(
    responder: Callable[[str, str, str | None], dict[str, Any]],
) -> tuple[CoinbaseReadPreviewClient, list[tuple[str, str, str | None]]]:
    calls: list[tuple[str, str, str | None]] = []
    client = CoinbaseReadPreviewClient(enable_response_cache=False)

    def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
        timeout: int,
    ) -> tuple[int, dict[str, str], str]:
        del headers, timeout
        calls.append((method, url, body))
        return 200, {}, json.dumps(responder(method, url, body))

    client.set_transport_for_testing(transport)
    return client, calls


def test_client_exposes_only_named_read_and_preview_operations() -> None:
    def responder(method: str, url: str, body: str | None) -> dict[str, Any]:
        if url.endswith("/key_permissions"):
            return {"can_view": True}
        if url.endswith("/orders/preview"):
            return {"order_total": "10"}
        raise AssertionError(url)

    client, calls = _client(responder)

    assert client.get_key_permissions() == {"can_view": True}
    assert client.preview_order({"product_id": "BTC-USD"}) == {"order_total": "10"}
    assert not hasattr(client, "place_order")
    assert [call[0] for call in calls] == ["GET", "POST"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v3/brokerage/orders"),
        ("DELETE", "/api/v3/brokerage/orders/123"),
        ("GET", "/api/v3/brokerage/portfolios"),
    ],
)
def test_direct_transport_escape_is_denied(method: str, path: str) -> None:
    client, calls = _client(lambda *_: {})

    with pytest.raises(PermissionDeniedError, match="blocked"):
        client._request(method, path)

    assert calls == []


def test_generic_http_helpers_are_denied() -> None:
    client, calls = _client(lambda *_: {})

    with pytest.raises(PermissionDeniedError, match="generic Coinbase GET"):
        client.get("/api/v3/brokerage/accounts")
    with pytest.raises(PermissionDeniedError, match="generic Coinbase POST"):
        client.post("/api/v3/brokerage/orders/preview", {})
    with pytest.raises(PermissionDeniedError, match="generic Coinbase DELETE"):
        client.delete("/api/v3/brokerage/orders/123")

    assert calls == []


def test_accounts_pagination_requires_explicit_has_next() -> None:
    client, _ = _client(lambda *_: {"accounts": []})

    with pytest.raises(InvalidRequestError, match="pagination"):
        client.list_all_accounts()


def test_order_history_uses_documented_batch_endpoint() -> None:
    client, calls = _client(lambda *_: {"orders": [], "has_next": False})

    assert client.list_all_orders() == {"orders": []}
    assert calls[0][1].endswith("/api/v3/brokerage/orders/historical/batch?limit=250")


def test_product_read_rejects_path_injection() -> None:
    client, calls = _client(lambda *_: {})

    with pytest.raises(InvalidRequestError, match="product ID"):
        client.get_product("BTC-USD/../../orders")

    assert calls == []
