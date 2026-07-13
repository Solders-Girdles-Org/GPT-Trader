from __future__ import annotations

import pytest

from gpt_trader.features.brokerages.robinhood.crypto.credentials import (
    resolve_robinhood_crypto_credentials,
)


def test_credentials_default_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBINHOOD_CRYPTO_API_KEY", raising=False)
    monkeypatch.delenv("ROBINHOOD_CRYPTO_PRIVATE_KEY", raising=False)

    assert resolve_robinhood_crypto_credentials() is None


def test_credentials_require_complete_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBINHOOD_CRYPTO_API_KEY", "api-key")
    monkeypatch.delenv("ROBINHOOD_CRYPTO_PRIVATE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="incomplete"):
        resolve_robinhood_crypto_credentials()


def test_credentials_are_immutable_and_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBINHOOD_CRYPTO_API_KEY", " api-key ")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_PRIVATE_KEY", " private-key ")

    credentials = resolve_robinhood_crypto_credentials()

    assert credentials is not None
    assert credentials.api_key == "api-key"
    assert credentials.private_key == "private-key"
    assert "api-key" not in repr(credentials)
    assert "private-key" not in repr(credentials)
    with pytest.raises(AttributeError):
        credentials.api_key = "changed"  # type: ignore[misc]
