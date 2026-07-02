"""Shared harness for Coinbase translator-level contract tests.

These fixtures wire a real ``CoinbaseClient`` + ``CoinbaseRestService`` stack
with the HTTP transport replaced by :class:`RecordedTransport`, which serves
recorded Coinbase Advanced Trade REST payloads from ``tests/fixtures/coinbase``.
Everything between the service facade and the wire — endpoint resolution, URL
building, response parsing, and domain-model translation — is production code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient
from gpt_trader.features.brokerages.coinbase.endpoints import CoinbaseEndpoints
from gpt_trader.features.brokerages.coinbase.market_data_service import MarketDataService
from gpt_trader.features.brokerages.coinbase.models import APIConfig
from gpt_trader.features.brokerages.coinbase.rest_service import CoinbaseRestService
from gpt_trader.features.brokerages.coinbase.utilities import ProductCatalog
from gpt_trader.persistence.event_store import EventStore

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "coinbase"


def load_coinbase_fixture(name: str) -> dict[str, Any]:
    """Load a recorded Advanced Trade payload from tests/fixtures/coinbase."""
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


@dataclass
class RecordedRequest:
    """One HTTP request captured at the transport boundary."""

    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    body: dict[str, Any] | None = None


class RecordedTransport:
    """Transport stub that serves recorded fixture payloads.

    Injected via ``CoinbaseClientBase.set_transport_for_testing`` so requests
    flow through the real client machinery (endpoint map, retries, response
    parsing) and stop only at the socket. Unrouted paths return 404 so a test
    hitting an unexpected endpoint fails loudly.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], dict[str, Any]] = {}
        self.requests: list[RecordedRequest] = []

    def route(self, method: str, path: str, payload: dict[str, Any]) -> None:
        self._routes[(method.upper(), path)] = payload

    def route_fixture(self, method: str, path: str, fixture_name: str) -> None:
        self.route(method, path, load_coinbase_fixture(fixture_name))

    def requests_for(self, method: str, path: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == method.upper() and r.path == path]

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
        timeout: float,
    ) -> tuple[int, dict[str, str], str]:
        parsed = urlparse(url)
        self.requests.append(
            RecordedRequest(
                method=method.upper(),
                path=parsed.path,
                query=parse_qs(parsed.query),
                body=json.loads(body) if body else None,
            )
        )
        payload = self._routes.get((method.upper(), parsed.path))
        if payload is None:
            return (
                404,
                {},
                json.dumps(
                    {
                        "error": "NOT_FOUND",
                        "message": f"No contract fixture routed for {method} {parsed.path}",
                    }
                ),
            )
        return 200, {}, json.dumps(payload)


@pytest.fixture
def transport() -> RecordedTransport:
    return RecordedTransport()


@pytest.fixture
def coinbase_service(transport: RecordedTransport) -> CoinbaseRestService:
    """Real CoinbaseRestService stack over the recorded transport (no network)."""
    config = APIConfig(
        api_key="contract-test-key",
        api_secret="contract-test-secret",
        passphrase=None,
        base_url="https://api.coinbase.com",
        api_mode="advanced",
    )
    client = CoinbaseClient(
        base_url=config.base_url,
        auth=None,
        api_mode=config.api_mode,
    )
    client.set_transport_for_testing(transport)
    return CoinbaseRestService(
        client=client,
        endpoints=CoinbaseEndpoints(config),
        config=config,
        product_catalog=ProductCatalog(),
        market_data=MarketDataService(symbols=[]),
        event_store=EventStore(),
    )
