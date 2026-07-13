"""Fail-closed Coinbase transport for account reads and order previews."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import requests

from gpt_trader.features.brokerages.coinbase.client.base import CoinbaseClientBase
from gpt_trader.features.brokerages.coinbase.client.constants import BASE_URL
from gpt_trader.features.brokerages.coinbase.errors import (
    InvalidRequestError,
    PermissionDeniedError,
)


class CoinbaseReadPreviewClient(CoinbaseClientBase):
    """Expose only the HTTP routes approved for read/preview account access.

    The base transport remains responsible for authentication, retries, and
    response handling. This class owns the capability boundary: even direct
    calls to ``_request`` cannot reach an undeclared route or method.
    """

    _STATIC_ROUTES = frozenset(
        {
            ("GET", "/api/v3/brokerage/key_permissions"),
            ("GET", "/api/v3/brokerage/accounts"),
            ("GET", "/api/v3/brokerage/cfm/balance_summary"),
            ("GET", "/api/v3/brokerage/cfm/positions"),
            ("GET", "/api/v3/brokerage/orders/historical/batch"),
            ("POST", "/api/v3/brokerage/orders/preview"),
        }
    )
    _PRODUCT_ROUTE = re.compile(r"^/api/v3/brokerage/products/[^/?]+$")
    _PAGINATED_ROUTES = frozenset(
        {
            "/api/v3/brokerage/accounts",
            "/api/v3/brokerage/orders/historical/batch",
        }
    )

    def __init__(self, base_url: str = BASE_URL, **kwargs: Any) -> None:
        if base_url != BASE_URL:
            raise PermissionDeniedError(
                "Coinbase read/preview access requires the canonical API URL"
            )
        super().__init__(base_url=base_url, **kwargs)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> dict:
        normalized_method = method.upper()
        normalized_path = self._validate_target(normalized_method, path, absolute=False)
        route = (normalized_method, normalized_path)
        product_read = normalized_method == "GET" and self._PRODUCT_ROUTE.fullmatch(normalized_path)
        if route not in self._STATIC_ROUTES and not product_read:
            raise PermissionDeniedError(
                f"Coinbase read/preview client blocked {normalized_method} {normalized_path}"
            )
        return super()._request(normalized_method, path, payload)

    def _perform_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict | None,
    ) -> requests.Response:
        self._validate_target(method.upper(), url, absolute=True)
        if self._transport:
            return super()._perform_request(method, url, headers, payload)

        response = self.session.request(
            method,
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise PermissionDeniedError("Coinbase read/preview redirects are disabled")
        self._validate_target(method.upper(), response.url, absolute=True)
        return response

    def _validate_target(self, method: str, target: str, *, absolute: bool) -> str:
        parsed = urlsplit(target)
        if parsed.fragment or parsed.username is not None or parsed.password is not None:
            raise PermissionDeniedError("Coinbase read/preview URL is not canonical")

        if absolute:
            if parsed.scheme != "https" or parsed.netloc != "api.coinbase.com":
                raise PermissionDeniedError("Coinbase read/preview URL is not canonical")
        elif parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            raise PermissionDeniedError("Coinbase read/preview paths must be relative API paths")

        path = parsed.path
        route = (method, path)
        product_read = method == "GET" and self._PRODUCT_ROUTE.fullmatch(path)
        if route not in self._STATIC_ROUTES and not product_read:
            raise PermissionDeniedError(f"Coinbase read/preview client blocked {method} {path}")

        try:
            query_items = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise PermissionDeniedError("Coinbase read/preview query is malformed") from exc
        query = dict(query_items)
        if len(query) != len(query_items):
            raise PermissionDeniedError("Coinbase read/preview query keys must be unique")
        if path in self._PAGINATED_ROUTES:
            if query.get("limit") != "250" or not set(query).issubset({"limit", "cursor"}):
                raise PermissionDeniedError("Coinbase pagination query is not allowlisted")
            if "cursor" in query and not query["cursor"]:
                raise PermissionDeniedError("Coinbase pagination cursor is empty")
        elif query:
            raise PermissionDeniedError("Coinbase read/preview query is not allowlisted")
        return path

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        raise PermissionDeniedError("generic Coinbase GET access is disabled")

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        raise PermissionDeniedError("generic Coinbase POST access is disabled")

    def delete(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        raise PermissionDeniedError("generic Coinbase DELETE access is disabled")

    def get_key_permissions(self) -> dict[str, Any]:
        return self._request("GET", self._get_endpoint_path("key_permissions"))

    def list_all_accounts(self) -> dict[str, Any]:
        endpoint = self._get_endpoint_path("accounts")
        return self._collect_pages(endpoint=endpoint, collection_key="accounts")

    def get_cfm_balance_summary(self) -> dict[str, Any]:
        return self._request("GET", self._get_endpoint_path("cfm_balance_summary"))

    def list_cfm_positions(self) -> dict[str, Any]:
        return self._request("GET", self._get_endpoint_path("cfm_positions"))

    def get_product(self, product_id: str) -> dict[str, Any]:
        if not product_id or any(character in product_id for character in ("/", "?", "#")):
            raise InvalidRequestError("Coinbase product ID is malformed")
        path = self._get_endpoint_path("product", product_id=product_id)
        return self._request("GET", path)

    def preview_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._get_endpoint_path("order_preview"), payload)

    def list_all_orders(self) -> dict[str, Any]:
        endpoint = f"{self._get_endpoint_path('orders_historical')}/batch"
        return self._collect_pages(endpoint=endpoint, collection_key="orders")

    def _collect_pages(self, *, endpoint: str, collection_key: str) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
            path = f"{endpoint}?limit=250"
            if cursor is not None:
                path = f"{path}&cursor={quote(cursor, safe='')}"
            response = self._request("GET", path)
            if not isinstance(response, dict) or not isinstance(response.get(collection_key), list):
                raise InvalidRequestError(f"Coinbase {collection_key} response is malformed")
            page = response[collection_key]
            if any(not isinstance(row, dict) for row in page):
                raise InvalidRequestError(f"Coinbase {collection_key} response is malformed")
            rows.extend(page)

            if "has_next" not in response or type(response["has_next"]) is not bool:
                raise InvalidRequestError(f"Coinbase {collection_key} pagination is malformed")
            if not response["has_next"]:
                break

            next_cursor = response.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise InvalidRequestError(f"Coinbase {collection_key} next cursor is missing")
            if next_cursor in seen_cursors:
                raise InvalidRequestError(f"Coinbase {collection_key} cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return {collection_key: rows}


__all__ = ["CoinbaseReadPreviewClient"]
