"""Environment-only Robinhood Crypto credential resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RobinhoodCryptoCredentials:
    api_key: str = field(repr=False)
    private_key: str = field(repr=False)


def resolve_robinhood_crypto_credentials() -> RobinhoodCryptoCredentials | None:
    """Return a complete credential pair without logging secret material."""
    api_key = os.getenv("ROBINHOOD_CRYPTO_API_KEY", "").strip()
    private_key = os.getenv("ROBINHOOD_CRYPTO_PRIVATE_KEY", "").strip()
    if not api_key and not private_key:
        return None
    if not api_key or not private_key:
        raise RuntimeError("Robinhood Crypto credentials are incomplete")
    return RobinhoodCryptoCredentials(api_key=api_key, private_key=private_key)


__all__ = ["RobinhoodCryptoCredentials", "resolve_robinhood_crypto_credentials"]
