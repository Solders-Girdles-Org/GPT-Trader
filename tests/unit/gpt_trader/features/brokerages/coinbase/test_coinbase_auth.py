"""Consolidated Coinbase authentication tests."""

from __future__ import annotations

import re

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from gpt_trader.features.brokerages.coinbase import auth as auth_module
from gpt_trader.features.brokerages.coinbase.auth import CDPJWTAuth, SimpleAuth
from gpt_trader.features.brokerages.coinbase.models import APIConfig
from tests.unit.gpt_trader.features.brokerages.coinbase.helpers import CoinbaseBrokerage


def test_cdp_jwt_auth_generates_valid_headers(monkeypatch):
    """Test that CDPJWTAuth generates proper JWT headers."""
    # Mock JWT generation to avoid needing a real EC key
    monkeypatch.setattr(CDPJWTAuth, "generate_jwt", lambda self, method, path: "mock_jwt_token")

    auth = CDPJWTAuth(api_key="test_key", private_key="mock_private_key")
    headers = auth.get_headers("GET", "/api/v3/brokerage/products")

    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer mock_jwt_token"
    assert headers["Content-Type"] == "application/json"


def test_simple_auth_generates_valid_headers(monkeypatch):
    """Test that SimpleAuth generates proper JWT headers."""
    # Mock JWT generation to avoid needing a real EC key
    monkeypatch.setattr(SimpleAuth, "generate_jwt", lambda self, method, path: "mock_jwt_token")

    auth = SimpleAuth(key_name="test_key", private_key="mock_private_key")
    headers = auth.get_headers("POST", "/api/v3/brokerage/orders")

    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer mock_jwt_token"
    assert headers["Content-Type"] == "application/json"


def test_broker_auth_selection_uses_cdp_jwt():
    """Test that CoinbaseBrokerage uses CDPJWTAuth when CDP keys are provided."""
    config = APIConfig(
        base_url="https://api.coinbase.com",
        sandbox=True,
        api_mode="advanced",
        enable_derivatives=True,
        api_key="",
        api_secret="",
        passphrase=None,
        cdp_api_key="test_cdp_key",
        cdp_private_key="mock_private_key",
    )
    broker = CoinbaseBrokerage(config)
    assert isinstance(broker.client.auth, CDPJWTAuth)


def test_broker_auth_fallback_uses_simple_auth():
    """Test that CoinbaseBrokerage uses SimpleAuth when only api_key/api_secret provided."""
    config = APIConfig(
        base_url="https://api.coinbase.com",
        sandbox=True,
        api_mode="advanced",
        enable_derivatives=True,
        api_key="test_api_key",
        api_secret="mock_private_key",
        passphrase=None,
    )
    broker = CoinbaseBrokerage(config)
    assert isinstance(broker.client.auth, SimpleAuth)


@pytest.mark.parametrize("auth_type", [CDPJWTAuth, SimpleAuth])
@pytest.mark.parametrize("escaped_sec1", [False, True])
@pytest.mark.parametrize(
    "request_path",
    ["/api/v3/brokerage/products", "https://api.coinbase.com/api/v3/brokerage/products?limit=1"],
)
def test_jwt_signing_round_trip_preserves_coinbase_claims(
    auth_type, request_path, escaped_sec1, monkeypatch, fake_clock
):
    """Exercise the real PyJWT ES256 transport without account keys or a network."""
    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        (
            serialization.PrivateFormat.TraditionalOpenSSL
            if escaped_sec1
            else serialization.PrivateFormat.PKCS8
        ),
        serialization.NoEncryption(),
    ).decode()
    if escaped_sec1:
        private_pem = private_pem.replace("\n", "\\n")
    key_name = "organizations/test/apiKeys/test"
    monkeypatch.setattr(auth_module.time, "time", fake_clock.time)
    auth = auth_type(key_name, private_pem)

    token = auth.generate_jwt("GET", request_path)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == key_name
    assert re.fullmatch(r"[0-9a-f]{64}", header["nonce"])
    claims = jwt.decode(token, key.public_key(), algorithms=["ES256"], issuer="cdp")
    assert claims == {
        "sub": key_name,
        "iss": "cdp",
        "nbf": int(fake_clock.time()),
        "exp": int(fake_clock.time()) + 120,
        "uri": "GET api.coinbase.com/api/v3/brokerage/products",
    }
    wrong_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, wrong_key.public_key(), algorithms=["ES256"])
