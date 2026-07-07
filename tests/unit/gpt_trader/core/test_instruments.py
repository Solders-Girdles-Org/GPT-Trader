"""Tests for the structured instrument taxonomy (core/instruments.py)."""

from __future__ import annotations

import pytest

from gpt_trader.core.instruments import (
    AssetClass,
    Instrument,
    InstrumentParseError,
    ProductType,
)


@pytest.mark.parametrize("raw", ["BTC-USD", "ETH-USD", "SOL-USDC", "1INCH-USD"])
def test_parse_classifies_base_quote_pairs_as_crypto_spot(raw: str) -> None:
    instrument = Instrument.parse(raw)

    assert instrument == Instrument(
        symbol=raw,
        asset_class=AssetClass.CRYPTO,
        product_type=ProductType.SPOT,
    )


@pytest.mark.parametrize("raw", ["AAPL", "F", "GOOGL", "TSLA"])
def test_parse_classifies_bare_uppercase_tickers_as_equity(raw: str) -> None:
    instrument = Instrument.parse(raw)

    assert instrument == Instrument(
        symbol=raw,
        asset_class=AssetClass.EQUITY,
        product_type=ProductType.SPOT,
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "btc-usd",  # lowercase pair
        "aapl",  # lowercase ticker
        "BTC-USD-PERP",  # more than one dash: contract structure is out of scope
        "BTC-",  # missing quote segment
        "-USD",  # missing base segment
        "BRK.B",  # share-class dot: ambiguous, refuse to guess
        "BTC/USD",  # wrong separator
        "AAPL ",  # stray whitespace
        "ES2026",  # digits in a bare token: not a bare equity ticker
    ],
)
def test_parse_is_loud_on_ambiguous_strings(raw: str) -> None:
    with pytest.raises(InstrumentParseError, match="ambiguous instrument string"):
        Instrument.parse(raw)


def test_parse_error_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        Instrument.parse("???")


def test_instrument_preserves_the_exact_symbol_string() -> None:
    assert Instrument.parse("BTC-USD").symbol == "BTC-USD"
    assert Instrument.parse("AAPL").symbol == "AAPL"


def test_settlement_days_derive_from_asset_class() -> None:
    # Cash-account settlement is data keyed off the asset class (#1231):
    # crypto spot settles immediately, US equities settle T+1.
    assert Instrument.parse("BTC-USD").settlement_days == 0
    assert Instrument.parse("AAPL").settlement_days == 1


def test_settlement_days_are_total_over_the_asset_class_vocabulary() -> None:
    # Every classifiable instrument must have a settlement answer; a new
    # asset class without one would KeyError here before it could reach the
    # buying-power check.
    for asset_class in AssetClass:
        instrument = Instrument(
            symbol="X",
            asset_class=asset_class,
            product_type=ProductType.SPOT,
        )
        assert instrument.settlement_days >= 0


def test_product_type_vocabulary_is_unchanged() -> None:
    # Persisted trade-idea records serialize these exact values; the enum
    # moved to core but its vocabulary must not drift.
    assert [member.value for member in ProductType] == [
        "spot",
        "futures",
        "options",
        "event_contract",
        "other",
    ]


def test_feature_layer_reexports_the_same_product_type() -> None:
    from gpt_trader.features.trade_ideas import ProductType as FeatureProductType

    assert FeatureProductType is ProductType
