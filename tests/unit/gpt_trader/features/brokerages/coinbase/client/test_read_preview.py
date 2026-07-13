from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
import requests

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


@pytest.mark.parametrize(
    "target",
    [
        "https://api.coinbase.com/api/v3/brokerage/accounts?limit=250",
        "https://evil.example/api/v3/brokerage/accounts?limit=250",
        "//api.coinbase.com/api/v3/brokerage/accounts?limit=250",
    ],
)
def test_request_rejects_absolute_or_network_paths(target: str) -> None:
    client, calls = _client(lambda *_: {})

    with pytest.raises(PermissionDeniedError, match="relative API paths"):
        client._request("GET", target)

    assert calls == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.coinbase.com",
        "https://api.coinbase.com:443",
        "https://evil.example",
    ],
)
def test_client_rejects_noncanonical_base_url(base_url: str) -> None:
    with pytest.raises(PermissionDeniedError, match="canonical API URL"):
        CoinbaseReadPreviewClient(base_url=base_url)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v3/brokerage/accounts?limit=250&unexpected=1",
        "/api/v3/brokerage/accounts?limit=100",
        "/api/v3/brokerage/accounts?limit=250&limit=250",
        "/api/v3/brokerage/key_permissions?cursor=unexpected",
    ],
)
def test_request_rejects_unallowlisted_query(path: str) -> None:
    client, calls = _client(lambda *_: {})

    with pytest.raises(PermissionDeniedError, match="query|pagination"):
        client._request("GET", path)

    assert calls == []


def test_low_level_request_revalidates_method_and_absolute_url() -> None:
    client, calls = _client(lambda *_: {})

    with pytest.raises(PermissionDeniedError, match="blocked"):
        client._perform_request(
            "POST",
            "https://api.coinbase.com/api/v3/brokerage/orders",
            {},
            None,
        )

    assert calls == []


def test_real_transport_disables_and_rejects_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseReadPreviewClient(enable_response_cache=False)
    request_kwargs: dict[str, Any] = {}

    def request(*args: Any, **kwargs: Any) -> requests.Response:
        del args
        request_kwargs.update(kwargs)
        response = requests.Response()
        response.status_code = 302
        response.url = "https://api.coinbase.com/api/v3/brokerage/accounts?limit=250"
        response.headers["Location"] = "https://evil.example/collect"
        return response

    monkeypatch.setattr(client.session, "request", request)

    with pytest.raises(PermissionDeniedError, match="redirects are disabled"):
        client.list_all_accounts()

    assert request_kwargs["allow_redirects"] is False


def test_real_transport_rejects_final_url_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseReadPreviewClient(enable_response_cache=False)

    def request(*args: Any, **kwargs: Any) -> requests.Response:
        del args, kwargs
        response = requests.Response()
        response.status_code = 200
        response.url = "https://evil.example/api/v3/brokerage/accounts?limit=250"
        return response

    monkeypatch.setattr(client.session, "request", request)

    with pytest.raises(PermissionDeniedError, match="URL is not canonical"):
        client.list_all_accounts()


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
