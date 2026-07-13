"""Real API smoke coverage for Coinbase CDP permissions endpoint."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    load_dotenv = None  # type: ignore[assignment]

from gpt_trader.features.brokerages.coinbase.auth import CDPJWTAuth
from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient

pytestmark = [
    pytest.mark.real_api,
    pytest.mark.requires_network,
    pytest.mark.requires_secrets,
]


@dataclass(frozen=True)
class CDPCredentials:
    api_key: str
    private_key: str


@pytest.fixture
def coinbase_cdp_credentials() -> CDPCredentials:
    """Provide Coinbase CDP API credentials or skip when unavailable."""
    if load_dotenv is not None:
        load_dotenv()

    api_key = os.getenv("COINBASE_PROD_CDP_API_KEY")
    private_key = os.getenv("COINBASE_PROD_CDP_PRIVATE_KEY")

    if not api_key or not private_key:
        pytest.skip("COINBASE_PROD_CDP_* credentials not set")

    return CDPCredentials(api_key=api_key, private_key=private_key)


def test_live_permission_check(coinbase_cdp_credentials: CDPCredentials) -> None:
    auth = CDPJWTAuth(
        api_key_name=coinbase_cdp_credentials.api_key,
        private_key_pem=coinbase_cdp_credentials.private_key,
        base_host="api.coinbase.com",
    )

    client = CoinbaseClient(
        base_url="https://api.coinbase.com",
        auth=auth,
        api_mode="advanced",
    )

    perms = client.get_key_permissions()

    assert "can_view" in perms
    assert "can_trade" in perms
    assert "can_transfer" in perms
    assert "can_receive" in perms
    assert "portfolio_uuid" in perms
    assert "portfolio_type" in perms
