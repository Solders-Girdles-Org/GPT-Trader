"""Structurally GET-only Robinhood Crypto v2 client."""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gpt_trader.features.brokerages.robinhood.crypto.errors import (
    RobinhoodCryptoClientViolation,
    RobinhoodCryptoTransportError,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import (
    RobinhoodCryptoAccount,
    RobinhoodCryptoEstimate,
    RobinhoodCryptoHolding,
    RobinhoodCryptoOrder,
    RobinhoodCryptoQuote,
    RobinhoodCryptoTradingPair,
)
from gpt_trader.features.brokerages.robinhood.crypto.parsing import (
    parse_account,
    parse_estimate,
    parse_holding,
    parse_order,
    parse_quote,
    parse_trading_pair,
    result_rows,
)

ROBINHOOD_CRYPTO_BASE_URL = "https://trading.robinhood.com"


class _ResponseProtocol(Protocol):
    status_code: int
    url: str

    def json(self) -> Any: ...


class _SessionProtocol(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> _ResponseProtocol: ...

    def close(self) -> None: ...


def _unix_time() -> int:
    return int(time.time())


class RobinhoodCryptoClient:
    """Expose six typed GET operations with no generic HTTP or session surface."""

    _ACCOUNTS = "/api/v2/crypto/trading/accounts/"
    _HOLDINGS = "/api/v2/crypto/trading/holdings/"
    _ORDERS = "/api/v2/crypto/trading/orders/"
    _TRADING_PAIRS = "/api/v2/crypto/trading/trading_pairs/"
    _QUOTES = "/api/v2/crypto/marketdata/best_bid_ask/"
    _ESTIMATED_PRICE = "/api/v2/crypto/trading/estimated_price/"
    _ROUTES = frozenset({_ACCOUNTS, _HOLDINGS, _ORDERS, _TRADING_PAIRS, _QUOTES, _ESTIMATED_PRICE})
    _QUERY_KEYS = {
        _ACCOUNTS: frozenset({"cursor", "limit"}),
        _HOLDINGS: frozenset({"account_number", "asset_code", "cursor", "limit"}),
        _ORDERS: frozenset({"account_number", "cursor"}),
        _TRADING_PAIRS: frozenset({"symbol", "cursor", "limit"}),
        _QUOTES: frozenset({"symbol"}),
        _ESTIMATED_PRICE: frozenset({"symbol", "side", "quantity"}),
    }
    _REPEATABLE_KEYS = frozenset({"asset_code", "symbol"})

    def __init__(
        self,
        *,
        api_key: str,
        private_key: str,
        expected_account_number: str,
        session: _SessionProtocol | None = None,
        timestamp: Callable[[], int] = _unix_time,
        timeout: float = 15.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key is required")
        if not expected_account_number.strip():
            raise ValueError("expected_account_number is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._api_key = api_key.strip()
        self._private_key = self._decode_private_key(private_key)
        self._expected_account_number = expected_account_number.strip()
        self._session: _SessionProtocol = session if session is not None else requests.Session()
        self._timestamp = timestamp
        self._timeout = timeout
        self._closed = False

    @staticmethod
    def _decode_private_key(value: str) -> Ed25519PrivateKey:
        try:
            raw = base64.b64decode(value.strip(), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Robinhood Crypto private key is not valid base64") from exc
        if len(raw) != 32:
            raise ValueError("Robinhood Crypto private key must contain 32 bytes")
        return Ed25519PrivateKey.from_private_bytes(raw)

    def list_accounts(self) -> tuple[RobinhoodCryptoAccount, ...]:
        rows = self._collect_pages(self._ACCOUNTS)
        accounts = tuple(sorted(parse_account(row) for row in rows))
        if len(accounts) != 1 or accounts[0].account_number != self._expected_account_number:
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto account response does not match the expected account"
            )
        return accounts

    def list_holdings(self) -> tuple[RobinhoodCryptoHolding, ...]:
        rows = self._collect_pages(
            self._HOLDINGS,
            (("account_number", self._expected_account_number),),
        )
        holdings = tuple(sorted(parse_holding(row) for row in rows))
        if any(item.account_number != self._expected_account_number for item in holdings):
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto holding response does not match the expected account"
            )
        return holdings

    def list_orders(self) -> tuple[RobinhoodCryptoOrder, ...]:
        rows = self._collect_pages(
            self._ORDERS,
            (("account_number", self._expected_account_number),),
        )
        orders = tuple(sorted(parse_order(row) for row in rows))
        if any(item.account_number != self._expected_account_number for item in orders):
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto order response does not match the expected account"
            )
        return orders

    def list_trading_pairs(
        self, symbols: Sequence[str] = ()
    ) -> tuple[RobinhoodCryptoTradingPair, ...]:
        normalized = self._symbols(symbols)
        rows = self._collect_pages(
            self._TRADING_PAIRS,
            tuple(("symbol", symbol) for symbol in normalized),
        )
        pairs = tuple(sorted(parse_trading_pair(row) for row in rows))
        if len({pair.symbol for pair in pairs}) != len(pairs):
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto trading-pair response contains duplicate symbols"
            )
        if normalized and {pair.symbol for pair in pairs} != set(normalized):
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto trading-pair response does not match requested symbols"
            )
        return pairs

    def get_quotes(self, symbols: Sequence[str]) -> tuple[RobinhoodCryptoQuote, ...]:
        normalized = self._symbols(symbols, required=True)
        payload = self._get_json(
            self._build_url(
                self._QUOTES,
                tuple(("symbol", symbol) for symbol in normalized),
            )
        )
        rows = result_rows(payload, "quotes")
        quotes = tuple(sorted(parse_quote(row) for row in rows))
        if len({quote.symbol for quote in quotes}) != len(quotes):
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto quote response contains duplicate symbols"
            )
        if {quote.symbol for quote in quotes} != set(normalized):
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto quote response does not match requested symbols"
            )
        return quotes

    def get_estimated_price(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
    ) -> RobinhoodCryptoEstimate:
        normalized_symbol = self._symbols((symbol,), required=True)[0]
        normalized_side = side.strip().lower()
        if normalized_side not in {"bid", "ask"}:
            raise ValueError("side must be bid or ask")
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("quantity must be positive and finite")
        payload = self._get_json(
            self._build_url(
                self._ESTIMATED_PRICE,
                (
                    ("symbol", normalized_symbol),
                    ("side", normalized_side),
                    ("quantity", str(quantity)),
                ),
            )
        )
        rows = result_rows(payload, "estimated-price")
        if len(rows) != 1:
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto estimated-price response must contain exactly one result"
            )
        estimate = parse_estimate(rows[0])
        if (
            estimate.symbol != normalized_symbol
            or estimate.side != normalized_side
            or estimate.quantity != quantity
        ):
            raise RobinhoodCryptoClientViolation(
                "Robinhood Crypto estimated-price response does not match the request"
            )
        return estimate

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._session.close()

    def __enter__(self) -> RobinhoodCryptoClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _collect_pages(
        self,
        path: str,
        params: Sequence[tuple[str, str]] = (),
    ) -> list[dict[str, Any]]:
        url = self._build_url(path, params)
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        while True:
            if url in seen:
                raise RobinhoodCryptoClientViolation("Robinhood Crypto pagination repeated")
            seen.add(url)
            if len(seen) > 1000:
                raise RobinhoodCryptoClientViolation("Robinhood Crypto pagination exceeded limit")
            payload = self._get_json(url)
            rows.extend(result_rows(payload, "paginated", paginated=True))
            if "next" not in payload or "previous" not in payload:
                raise RobinhoodCryptoClientViolation(
                    "Robinhood Crypto pagination fields are missing"
                )
            next_url = payload["next"]
            previous_url = payload["previous"]
            if previous_url is not None and not isinstance(previous_url, str):
                raise RobinhoodCryptoClientViolation(
                    "Robinhood Crypto previous-page URL is malformed"
                )
            if isinstance(previous_url, str):
                self._validate_url(previous_url)
                if urlsplit(previous_url).path != path:
                    raise RobinhoodCryptoClientViolation(
                        "Robinhood Crypto previous-page path changed"
                    )
            if next_url is None:
                break
            if not isinstance(next_url, str) or not next_url:
                raise RobinhoodCryptoClientViolation("Robinhood Crypto next-page URL is malformed")
            self._validate_url(next_url)
            if urlsplit(next_url).path != path:
                raise RobinhoodCryptoClientViolation("Robinhood Crypto pagination path changed")
            url = next_url
        return rows

    def _get_json(self, url: str) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("Robinhood Crypto client is closed")
        request_path = self._validate_url(url)
        timestamp = self._timestamp()
        if type(timestamp) is not int or timestamp <= 0:
            raise RobinhoodCryptoClientViolation("Robinhood Crypto timestamp is invalid")
        message = f"{self._api_key}{timestamp}{request_path}GET"
        signature = base64.b64encode(self._private_key.sign(message.encode())).decode()
        try:
            response = self._session.get(
                url,
                headers={
                    "Accept": "application/json",
                    "x-api-key": self._api_key,
                    "x-timestamp": str(timestamp),
                    "x-signature": signature,
                },
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RobinhoodCryptoTransportError("Robinhood Crypto request failed") from exc
        if 300 <= response.status_code < 400:
            raise RobinhoodCryptoClientViolation("Robinhood Crypto redirects are disabled")
        response_path = self._validate_url(response.url)
        if response.url != url or response_path != request_path:
            raise RobinhoodCryptoClientViolation("Robinhood Crypto response URL changed")
        if response.status_code < 200 or response.status_code >= 300:
            raise RobinhoodCryptoTransportError(
                f"Robinhood Crypto returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise RobinhoodCryptoClientViolation("Robinhood Crypto response is not JSON") from exc
        if not isinstance(payload, dict):
            raise RobinhoodCryptoClientViolation("Robinhood Crypto response must be an object")
        return payload

    def _build_url(self, path: str, params: Sequence[tuple[str, str]]) -> str:
        query = urlencode(list(params), doseq=True)
        url = f"{ROBINHOOD_CRYPTO_BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"
        self._validate_url(url)
        return url

    def _validate_url(self, url: str) -> str:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "trading.robinhood.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.path not in self._ROUTES
        ):
            raise RobinhoodCryptoClientViolation("Robinhood Crypto URL is not canonical")
        try:
            items = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise RobinhoodCryptoClientViolation("Robinhood Crypto query is malformed") from exc
        allowed = self._QUERY_KEYS[parsed.path]
        keys = [key for key, _ in items]
        if any(key not in allowed or not value for key, value in items):
            raise RobinhoodCryptoClientViolation("Robinhood Crypto query is not allowlisted")
        for key in set(keys):
            if key not in self._REPEATABLE_KEYS and keys.count(key) != 1:
                raise RobinhoodCryptoClientViolation("Robinhood Crypto query keys must be unique")
        values: dict[str, list[str]] = {}
        for key, value in items:
            values.setdefault(key, []).append(value)
        if parsed.path in {self._HOLDINGS, self._ORDERS}:
            if values.get("account_number") != [self._expected_account_number]:
                raise RobinhoodCryptoClientViolation(
                    "Robinhood Crypto request account does not match expected"
                )
        if parsed.path == self._QUOTES and set(values) != {"symbol"}:
            raise RobinhoodCryptoClientViolation("Robinhood Crypto quote query is incomplete")
        if parsed.path == self._ESTIMATED_PRICE:
            if set(values) != {"symbol", "side", "quantity"}:
                raise RobinhoodCryptoClientViolation(
                    "Robinhood Crypto estimated-price query is incomplete"
                )
            if values["side"] not in (["bid"], ["ask"]):
                raise RobinhoodCryptoClientViolation(
                    "Robinhood Crypto estimated-price side is unsupported"
                )
        if "limit" in values and (not values["limit"][0].isdigit()):
            raise RobinhoodCryptoClientViolation("Robinhood Crypto page limit is malformed")
        for symbol in values.get("symbol", []):
            if re.fullmatch(r"[A-Z0-9]+-USD", symbol) is None:
                raise RobinhoodCryptoClientViolation("Robinhood Crypto symbol is malformed")
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")

    @staticmethod
    def _symbols(symbols: Sequence[str], *, required: bool = False) -> tuple[str, ...]:
        normalized = tuple(symbol.strip().upper() for symbol in symbols)
        if required and not normalized:
            raise ValueError("at least one symbol is required")
        if any(re.fullmatch(r"[A-Z0-9]+-USD", symbol) is None for symbol in normalized):
            raise ValueError("Robinhood Crypto symbol is malformed")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Robinhood Crypto symbols must be unique")
        return normalized


__all__ = [
    "ROBINHOOD_CRYPTO_BASE_URL",
    "RobinhoodCryptoClient",
    "RobinhoodCryptoClientViolation",
    "RobinhoodCryptoTransportError",
]
