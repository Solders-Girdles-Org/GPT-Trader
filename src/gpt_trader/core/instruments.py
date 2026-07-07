"""Structured instrument taxonomy behind the opaque instrument string.

Persisted records (trade ideas, audit events, exports) keep the plain string
form — those records are content-hashed, so their serialization must never
change. This module supplies the structured view derived from that string:
an :class:`Instrument` value type plus a total, loud-on-ambiguity classifier
for the two legacy shapes (crypto spot pairs like ``BTC-USD`` and bare equity
tickers like ``AAPL``). See docs/decisions/venue-neutrality-posture.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class AssetClass(str, Enum):
    """Top-level asset-class vocabulary for instruments."""

    CRYPTO = "crypto"
    EQUITY = "equity"


class ProductType(str, Enum):
    """Product-structure vocabulary shared by trade ideas and budget layers."""

    SPOT = "spot"
    FUTURES = "futures"
    OPTIONS = "options"
    EVENT_CONTRACT = "event_contract"
    OTHER = "other"


class InstrumentParseError(ValueError):
    """Raised when an instrument string does not match a known shape.

    The classifier refuses to guess: anything that is not unambiguously a
    crypto ``BASE-QUOTE`` pair or a bare uppercase equity ticker is an error.
    """


# BASE-QUOTE pair, e.g. BTC-USD: uppercase alphanumeric segments, one dash.
_CRYPTO_PAIR = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")
# Bare uppercase ticker, e.g. AAPL: letters only.
_EQUITY_TICKER = re.compile(r"^[A-Z]+$")


@dataclass(frozen=True, slots=True)
class Instrument:
    """Structured identity of a tradeable instrument.

    ``symbol`` preserves the exact string used by persisted records, snapshot
    contracts, broker payloads, and busy-instrument tracking — those continue
    to key on the string; this type only adds structure on top of it.
    """

    symbol: str
    asset_class: AssetClass
    product_type: ProductType

    @classmethod
    def parse(cls, raw: str) -> Instrument:
        """Classify a legacy instrument string into a structured instrument.

        Rules (total, no guessing):

        - ``BASE-QUOTE`` pairs of uppercase alphanumeric segments
          (``BTC-USD``) -> crypto spot.
        - Bare uppercase tickers (``AAPL``) -> equity spot.
        - Anything else raises :class:`InstrumentParseError`.
        """
        if _CRYPTO_PAIR.fullmatch(raw):
            return cls(
                symbol=raw,
                asset_class=AssetClass.CRYPTO,
                product_type=ProductType.SPOT,
            )
        if _EQUITY_TICKER.fullmatch(raw):
            return cls(
                symbol=raw,
                asset_class=AssetClass.EQUITY,
                product_type=ProductType.SPOT,
            )
        raise InstrumentParseError(
            f"ambiguous instrument string {raw!r}: expected an uppercase "
            "BASE-QUOTE crypto pair (e.g. 'BTC-USD') or a bare uppercase "
            "equity ticker (e.g. 'AAPL')"
        )
