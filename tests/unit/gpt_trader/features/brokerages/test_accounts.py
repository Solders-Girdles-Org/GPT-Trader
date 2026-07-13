from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.core.instruments import AssetClass, ProductType
from gpt_trader.features.brokerages.accounts import (
    AccountIdentity,
    AccountObservation,
    AccountProvider,
    ObservedBalance,
    ObservedOptionPosition,
    ObservedPosition,
    OptionRight,
    PreviewKind,
    PreviewRequest,
    PreviewResult,
)


def test_account_identity_fingerprint_is_stable_without_exposing_identifiers() -> None:
    identity = AccountIdentity(
        provider=AccountProvider.COINBASE,
        account_id="account-secret-1234",
        portfolio_id="portfolio-secret-5678",
        account_type="individual",
        status="active",
        interface="advanced-trade-v3",
    )

    duplicate = AccountIdentity(
        provider=AccountProvider.COINBASE,
        account_id="account-secret-1234",
        portfolio_id="portfolio-secret-5678",
        account_type="individual",
        status="active",
        interface="advanced-trade-v3",
    )

    assert identity.fingerprint == duplicate.fingerprint
    assert "account-secret" not in identity.fingerprint
    assert "portfolio-secret" not in identity.fingerprint
    assert identity.to_dict()["account_id"] == "***************1234"
    assert identity.to_dict()["portfolio_id"] == "*****************5678"


@pytest.mark.parametrize("identifier", ["a", "ab", "abc", "abcd", "abcde"])
def test_account_identity_masks_short_identifiers(identifier: str) -> None:
    identity = AccountIdentity(
        provider=AccountProvider.COINBASE,
        account_id=identifier,
        interface="advanced-trade-v3",
    )

    masked = identity.to_dict()["account_id"]

    assert masked != identifier
    if len(identifier) <= 4:
        assert set(masked) == {"*"}
    else:
        assert masked == "*bcde"


def test_account_observation_serializes_distinct_asset_shapes() -> None:
    observed_at = datetime(2026, 7, 12, 15, 30, tzinfo=UTC)
    observation = AccountObservation(
        identity=AccountIdentity(
            provider=AccountProvider.ROBINHOOD_AGENTIC,
            account_id="agentic-account",
            interface="agentic-mcp",
        ),
        generated_at=observed_at,
        balances=(
            ObservedBalance(
                asset="USD",
                total=Decimal("100.25"),
                available=Decimal("90.25"),
                hold=Decimal("10"),
            ),
        ),
        positions=(
            ObservedPosition(
                instrument="AAPL",
                asset_class=AssetClass.EQUITY,
                product_type=ProductType.SPOT,
                quantity=Decimal("2"),
                average_cost=Decimal("180"),
            ),
        ),
        option_positions=(
            ObservedOptionPosition(
                contract_id="option-1",
                underlying="AAPL",
                expiration=date(2026, 8, 21),
                strike=Decimal("200"),
                right=OptionRight.CALL,
                multiplier=Decimal("100"),
                quantity=Decimal("1"),
                average_cost=Decimal("2.50"),
            ),
        ),
        buying_power={"equity": Decimal("75.50")},
        warnings=("agentic account is unfunded",),
        source_metadata={"tool_schema": "abc123"},
    )

    payload = observation.to_dict()

    assert payload["schema_version"] == "gpt-trader.account-observation.v1"
    assert payload["generated_at"] == "2026-07-12T15:30:00+00:00"
    assert payload["balances"][0]["total"] == "100.25"
    assert payload["positions"][0]["instrument"] == "AAPL"
    assert payload["positions"][0]["asset_class"] == "equity"
    assert payload["positions"][0]["product_type"] == "spot"
    assert payload["option_positions"][0] == {
        "contract_id": "option-1",
        "underlying": "AAPL",
        "expiration": "2026-08-21",
        "strike": "200",
        "right": "call",
        "multiplier": "100",
        "quantity": "1",
        "average_cost": "2.50",
        "market_price": None,
    }
    assert payload["buying_power"] == {"equity": "75.50"}


def test_account_observation_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="generated_at must include a timezone"):
        AccountObservation(
            identity=AccountIdentity(
                provider=AccountProvider.COINBASE,
                account_id="account-1",
                interface="advanced-trade-v3",
            ),
            generated_at=datetime(2026, 7, 12, 15, 30),
        )


def test_option_position_rejects_non_positive_multiplier() -> None:
    with pytest.raises(ValueError, match="multiplier must be positive"):
        ObservedOptionPosition(
            contract_id="option-1",
            underlying="AAPL",
            expiration=date(2026, 8, 21),
            strike=Decimal("200"),
            right=OptionRight.CALL,
            multiplier=Decimal("0"),
            quantity=Decimal("1"),
        )


@pytest.mark.parametrize("field_name", ["account_id", "interface"])
def test_account_identity_requires_binding_fields(field_name: str) -> None:
    values = {
        "provider": AccountProvider.COINBASE,
        "account_id": "account-1",
        "interface": "advanced-trade-v3",
    }
    values[field_name] = ""

    with pytest.raises(ValueError, match=field_name):
        AccountIdentity(**values)


def test_account_observation_rejects_non_finite_buying_power() -> None:
    with pytest.raises(ValueError, match="buying_power.equity must be finite"):
        AccountObservation(
            identity=AccountIdentity(
                provider=AccountProvider.COINBASE,
                account_id="account-1",
                interface="advanced-trade-v3",
            ),
            generated_at=datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
            buying_power={"equity": Decimal("NaN")},
        )


def test_balance_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="total must be finite"):
        ObservedBalance(asset="USD", total=Decimal("Infinity"))


def test_preview_request_requires_positive_quantity() -> None:
    with pytest.raises(ValueError, match="quantity must be positive"):
        PreviewRequest(
            instrument="BTC-USD",
            side="buy",
            quantity=Decimal("0"),
            order_type="market",
        )


def test_preview_request_normalizes_side_and_order_type() -> None:
    request = PreviewRequest(
        instrument="AAPL",
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        limit_price=Decimal("200"),
    )

    assert request.side is OrderSide.BUY
    assert request.order_type is OrderType.LIMIT
    assert request.to_dict()["side"] == "buy"
    assert request.to_dict()["order_type"] == "limit"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"instrument": ""}, "instrument is required"),
        ({"side": "buy_now"}, "unsupported side"),
        ({"order_type": "stop"}, "unsupported preview order type"),
        ({"order_type": "limit", "limit_price": None}, "limit price is required"),
        (
            {"order_type": "limit", "limit_price": Decimal("0")},
            "limit price must be positive",
        ),
        (
            {"order_type": "market", "limit_price": Decimal("1")},
            "market preview cannot include a limit price",
        ),
    ],
)
def test_preview_request_rejects_invalid_normalized_order(
    overrides: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "instrument": "AAPL",
        "side": "buy",
        "quantity": Decimal("1"),
        "order_type": "market",
        "limit_price": None,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        PreviewRequest(**values)


def test_preview_result_is_always_non_binding() -> None:
    request = PreviewRequest(
        instrument="BTC-USD",
        side="buy",
        quantity=Decimal("0.01"),
        order_type="market",
    )
    result = PreviewResult(
        provider=AccountProvider.ROBINHOOD_CRYPTO,
        kind=PreviewKind.PROVIDER_ESTIMATE,
        generated_at=datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
        identity_fingerprint="fingerprint",
        request=request,
        estimated_price=Decimal("100000"),
        estimated_fee=Decimal("1.25"),
    )

    assert result.to_dict()["non_binding"] is True


def test_account_observation_copies_attested_mappings() -> None:
    buying_power = {"equity": Decimal("100")}
    source_metadata = {"attestation": "v1"}
    observation = AccountObservation(
        identity=AccountIdentity(
            provider=AccountProvider.ROBINHOOD_AGENTIC,
            account_id="account-1",
            interface="agentic-mcp",
        ),
        generated_at=datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
        buying_power=buying_power,
        source_metadata=source_metadata,
    )

    buying_power["equity"] = Decimal("0")
    source_metadata["attestation"] = "changed"

    assert observation.to_dict()["buying_power"] == {"equity": "100"}
    assert observation.to_dict()["source_metadata"] == {"attestation": "v1"}


def test_account_observation_copies_sequence_evidence() -> None:
    balances = [ObservedBalance(asset="USD", total=Decimal("100"))]
    positions = [
        ObservedPosition(
            instrument="AAPL",
            asset_class=AssetClass.EQUITY,
            product_type=ProductType.SPOT,
            quantity=Decimal("1"),
        )
    ]
    option_positions = [
        ObservedOptionPosition(
            contract_id="option-1",
            underlying="AAPL",
            expiration=date(2026, 8, 21),
            strike=Decimal("200"),
            right=OptionRight.CALL,
            multiplier=Decimal("100"),
            quantity=Decimal("1"),
        )
    ]
    warnings = ["warning-1"]
    observation = AccountObservation(
        identity=AccountIdentity(
            provider=AccountProvider.ROBINHOOD_AGENTIC,
            account_id="account-1",
            interface="agentic-mcp",
        ),
        generated_at=datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
        balances=balances,
        positions=positions,
        option_positions=option_positions,
        warnings=warnings,
    )

    balances.clear()
    positions.clear()
    option_positions.clear()
    warnings.clear()

    payload = observation.to_dict()
    assert len(payload["balances"]) == 1
    assert len(payload["positions"]) == 1
    assert len(payload["option_positions"]) == 1
    assert payload["warnings"] == ["warning-1"]


def test_preview_result_requires_identity_fingerprint() -> None:
    request = PreviewRequest(
        instrument="AAPL",
        side="buy",
        quantity=Decimal("1"),
        order_type="market",
    )

    with pytest.raises(ValueError, match="identity_fingerprint is required"):
        PreviewResult(
            provider=AccountProvider.ROBINHOOD_AGENTIC,
            kind=PreviewKind.PROVIDER_SIMULATION,
            generated_at=datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
            identity_fingerprint="",
            request=request,
        )


def test_preview_result_copies_warning_and_error_evidence() -> None:
    warnings = ["warning-1"]
    errors = ["error-1"]
    result = PreviewResult(
        provider=AccountProvider.COINBASE,
        kind=PreviewKind.PROVIDER_SIMULATION,
        generated_at=datetime(2026, 7, 12, 15, 30, tzinfo=UTC),
        identity_fingerprint="fingerprint",
        request=PreviewRequest(
            instrument="BTC-USD",
            side="buy",
            quantity=Decimal("0.01"),
            order_type="market",
        ),
        warnings=warnings,
        errors=errors,
    )

    warnings.clear()
    errors.clear()

    assert result.to_dict()["warnings"] == ["warning-1"]
    assert result.to_dict()["errors"] == ["error-1"]
