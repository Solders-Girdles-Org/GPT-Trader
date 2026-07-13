from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import gpt_trader.cli.commands.account as account_cmd
from gpt_trader.features.brokerages.accounts import (
    AccountIdentity,
    AccountObservation,
    AccountProvider,
    PreviewKind,
    PreviewRequest,
    PreviewResult,
)
from gpt_trader.features.brokerages.robinhood.agentic.models import (
    RobinhoodAgenticEquityReviewEvidence,
    RobinhoodAgenticOptionReviewEvidence,
    RobinhoodAgenticOptionReviewRequest,
)


def _observation(provider: AccountProvider) -> AccountObservation:
    return AccountObservation(
        identity=AccountIdentity(
            provider=provider,
            account_id="account-1234",
            portfolio_id="account-1234",
            interface="typed-read-review",
        ),
        generated_at=datetime(2026, 7, 13, tzinfo=UTC),
        buying_power={"cash": Decimal("100")},
    )


def test_agentic_observe_and_equity_review_are_typed_and_close() -> None:
    closed = {"count": 0}
    observation = _observation(AccountProvider.ROBINHOOD_AGENTIC)

    class Access:
        async def read_account(self) -> AccountObservation:
            return observation

        async def review_equity_order(
            self, request: PreviewRequest
        ) -> RobinhoodAgenticEquityReviewEvidence:
            return RobinhoodAgenticEquityReviewEvidence(
                preview=PreviewResult(
                    provider=AccountProvider.ROBINHOOD_AGENTIC,
                    kind=PreviewKind.PROVIDER_SIMULATION,
                    generated_at=datetime(2026, 7, 13, tzinfo=UTC),
                    identity_fingerprint=observation.identity.fingerprint,
                    request=request,
                    estimated_total=Decimal("50"),
                ),
                order_checks_json="{}",
                quote_json='{"ask_price":"50"}',
                market_data_disclosure="non-binding",
            )

        async def close(self) -> None:
            closed["count"] += 1

    class Container:
        config = object()

        async def create_robinhood_agentic_read_review_access(self) -> Access:
            return Access()

    observed = account_cmd._handle_observe(
        Namespace(provider="robinhood-agentic", application_container=Container())
    )
    reviewed = account_cmd._handle_preview(
        Namespace(
            provider="robinhood-agentic",
            product_type="equity",
            instrument="AAPL",
            side="buy",
            quantity=Decimal("1"),
            order_type="market",
            limit_price=None,
            application_container=Container(),
        )
    )

    assert observed.data["identity"]["provider"] == "robinhood_agentic"
    assert reviewed.success is True
    assert reviewed.data["non_binding"] is True
    assert reviewed.data["provider_evidence"]["order_checks_json"] == "{}"
    assert closed["count"] == 2


def test_agentic_option_review_uses_provider_specific_shape() -> None:
    captured: list[RobinhoodAgenticOptionReviewRequest] = []

    class Access:
        async def review_option_order(
            self, request: RobinhoodAgenticOptionReviewRequest
        ) -> RobinhoodAgenticOptionReviewEvidence:
            captured.append(request)
            return RobinhoodAgenticOptionReviewEvidence(
                request=request,
                generated_at=datetime(2026, 7, 13, tzinfo=UTC),
                identity_fingerprint="fingerprint",
                order_checks_json="{}",
                quotes_json="[]",
                total_fee=Decimal("0.03"),
                collateral_json='{"account_number":"RH-PRIVATE-1234"}',
            )

        async def close(self) -> None:
            return None

    class Container:
        config = object()

        async def create_robinhood_agentic_read_review_access(self) -> Access:
            return Access()

    response = account_cmd._handle_preview(
        Namespace(
            provider="robinhood-agentic",
            product_type="option",
            instrument="option-id",
            side="buy",
            position_effect="open",
            quantity=Decimal("2"),
            order_type="limit",
            limit_price=Decimal("1.25"),
            stop_price=None,
            preview_time_in_force="gfd",
            market_hours="regular_hours",
            chain_symbol="AAPL",
            underlying_type="equity",
            application_container=Container(),
        )
    )

    assert response.success is True
    assert response.data["request"]["product_type"] == "option"
    assert response.data["estimated_fee"] == "0.03"
    assert response.data["non_binding"] is True
    assert response.data["provider_evidence"]["collateral_json"] == (
        '{"account_number":"<redacted>"}'
    )
    assert "RH-PRIVATE-1234" not in json.dumps(response.data)
    assert len(response.data["provider_evidence"]["collateral_sha256"]) == 64
    assert captured[0].quantity == 2


def test_agentic_rejected_review_retains_immutable_provider_evidence() -> None:
    class Access:
        async def review_equity_order(
            self, request: PreviewRequest
        ) -> RobinhoodAgenticEquityReviewEvidence:
            return RobinhoodAgenticEquityReviewEvidence(
                preview=PreviewResult(
                    provider=AccountProvider.ROBINHOOD_AGENTIC,
                    kind=PreviewKind.PROVIDER_SIMULATION,
                    generated_at=datetime(2026, 7, 13, tzinfo=UTC),
                    identity_fingerprint="fingerprint",
                    request=request,
                    errors=('provider order checks: {"buying_power":"insufficient"}',),
                ),
                order_checks_json='{"buying_power":"insufficient"}',
                quote_json="null",
                market_data_disclosure="non-binding",
            )

        async def close(self) -> None:
            return None

    class Container:
        config = object()

        async def create_robinhood_agentic_read_review_access(self) -> Access:
            return Access()

    response = account_cmd._handle_preview(
        Namespace(
            provider="robinhood-agentic",
            product_type="equity",
            instrument="AAPL",
            side="buy",
            quantity=Decimal("1"),
            order_type="market",
            limit_price=None,
            application_container=Container(),
        )
    )

    assert response.success is False
    assert response.errors[0].details["non_binding"] is True
    evidence = response.errors[0].details["evidence"]
    assert evidence["identity_fingerprint"] == "fingerprint"
    assert evidence["request"]["instrument"] == "AAPL"
    assert evidence["provider_evidence"]["order_checks_json"] == ('{"buying_power":"insufficient"}')
    assert evidence["provider_evidence"]["market_data_disclosure"] == "non-binding"


def test_agentic_observe_closes_session_after_failure() -> None:
    closed = {"value": False}

    class Access:
        async def read_account(self) -> AccountObservation:
            raise RuntimeError("provider unavailable")

        async def close(self) -> None:
            closed["value"] = True

    class Container:
        config = object()

        async def create_robinhood_agentic_read_review_access(self) -> Access:
            return Access()

    response = account_cmd._handle_observe(
        Namespace(provider="robinhood-agentic", application_container=Container())
    )

    assert response.success is False
    assert closed["value"] is True


@pytest.mark.parametrize(
    ("provider", "quantity", "message"),
    [
        ("coinbase", Decimal("1"), "accepted only by Robinhood Agentic"),
        ("robinhood-agentic", Decimal("1.5"), "whole number"),
    ],
)
def test_option_preview_rejects_wrong_provider_or_fractional_quantity_before_access(
    provider: str,
    quantity: Decimal,
    message: str,
) -> None:
    response = account_cmd._handle_preview(
        Namespace(
            provider=provider,
            product_type="option",
            instrument="option-id",
            side="buy",
            position_effect="open",
            quantity=quantity,
            order_type="limit",
            limit_price=Decimal("1"),
            stop_price=None,
            time_in_force="gfd",
            market_hours="regular_hours",
            chain_symbol=None,
            underlying_type=None,
        )
    )

    assert response.success is False
    assert message in response.errors[0].message


def test_equity_preview_rejects_option_only_time_or_market_overrides_before_access() -> None:
    response = account_cmd._handle_preview(
        Namespace(
            provider="robinhood-agentic",
            product_type="equity",
            instrument="AAPL",
            side="buy",
            quantity=Decimal("1"),
            order_type="market",
            limit_price=None,
            preview_time_in_force="gtc",
            market_hours="regular_curb_overnight_hours",
        )
    )

    assert response.success is False
    assert "option-specific arguments" in response.errors[0].message
