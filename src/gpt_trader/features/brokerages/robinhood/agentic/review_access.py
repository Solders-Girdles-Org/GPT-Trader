"""Typed, identity-bound equity and option review simulations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.accounts import (
    AccountProvider,
    PreviewKind,
    PreviewRequest,
    PreviewResult,
)
from gpt_trader.features.brokerages.robinhood.agentic.account_access import (
    RobinhoodAgenticAccountReader,
    _decimal,
)
from gpt_trader.features.brokerages.robinhood.agentic.errors import (
    RobinhoodAgenticViolation,
)
from gpt_trader.features.brokerages.robinhood.agentic.models import (
    RobinhoodAgenticEquityReviewEvidence,
    RobinhoodAgenticOptionReviewEvidence,
    RobinhoodAgenticOptionReviewRequest,
)
from gpt_trader.features.brokerages.robinhood.agentic.schemas import canonical_json
from gpt_trader.features.brokerages.robinhood.agentic.transport import (
    RobinhoodAgenticGatewayProtocol,
)


def _checks(data: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    checks = data.get("order_checks")
    if not isinstance(checks, dict):
        raise RobinhoodAgenticViolation("Robinhood Agentic order checks are malformed")
    encoded = canonical_json(checks)
    return encoded, (() if not checks else (f"provider order checks: {encoded}",))


class RobinhoodAgenticEquityReviewProvider:
    def __init__(
        self,
        *,
        gateway: RobinhoodAgenticGatewayProtocol,
        account_reader: RobinhoodAgenticAccountReader,
        expected_account_number: str,
        clock: Callable[[], datetime],
    ) -> None:
        if not account_reader.uses_gateway(gateway):
            raise ValueError("review gateway must match the attested account reader gateway")
        self._gateway = gateway
        self._reader = account_reader
        self._account = expected_account_number
        self._clock = clock

    async def review(self, request: PreviewRequest) -> RobinhoodAgenticEquityReviewEvidence:
        identity = await self._reader.observe_identity()
        arguments: dict[str, Any] = {
            "account_number": self._account,
            "symbol": request.instrument,
            "side": request.side.value.lower(),
            "type": request.order_type.value.lower(),
            "quantity": str(request.quantity),
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        }
        if request.order_type is OrderType.LIMIT:
            arguments["limit_price"] = str(request.limit_price)
        payload = await self._gateway.review_equity_order(arguments)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RobinhoodAgenticViolation("Robinhood Agentic equity review is malformed")
        if (
            data.get("symbol") != request.instrument
            or data.get("side") != request.side.value.lower()
            or data.get("type") != request.order_type.value.lower()
            or data.get("quantity") != str(request.quantity)
        ):
            raise RobinhoodAgenticViolation(
                "Robinhood Agentic equity review does not match the request"
            )
        checks_json, errors = _checks(data)
        quote = data.get("quote_data")
        if quote is not None and not isinstance(quote, dict):
            raise RobinhoodAgenticViolation("Robinhood Agentic equity quote is malformed")
        quote_json = canonical_json(quote)
        estimated_price: Decimal | None = None
        if quote:
            price_field = "ask_price" if request.side is OrderSide.BUY else "bid_price"
            estimated_price = _decimal(quote.get(price_field), price_field)
        estimated_total = None if estimated_price is None else estimated_price * request.quantity
        disclosure = data.get("market_data_disclosure", "")
        if not isinstance(disclosure, str):
            raise RobinhoodAgenticViolation("Robinhood Agentic market-data disclosure is malformed")
        return RobinhoodAgenticEquityReviewEvidence(
            preview=PreviewResult(
                provider=AccountProvider.ROBINHOOD_AGENTIC,
                kind=PreviewKind.PROVIDER_SIMULATION,
                generated_at=self._clock(),
                identity_fingerprint=identity.fingerprint,
                request=request,
                estimated_price=estimated_price,
                estimated_total=estimated_total,
                errors=errors,
            ),
            order_checks_json=checks_json,
            quote_json=quote_json,
            market_data_disclosure=disclosure,
        )


class RobinhoodAgenticOptionReviewProvider:
    def __init__(
        self,
        *,
        gateway: RobinhoodAgenticGatewayProtocol,
        account_reader: RobinhoodAgenticAccountReader,
        expected_account_number: str,
        clock: Callable[[], datetime],
    ) -> None:
        if not account_reader.uses_gateway(gateway):
            raise ValueError("review gateway must match the attested account reader gateway")
        self._gateway = gateway
        self._reader = account_reader
        self._account = expected_account_number
        self._clock = clock

    async def review(
        self, request: RobinhoodAgenticOptionReviewRequest
    ) -> RobinhoodAgenticOptionReviewEvidence:
        identity = await self._reader.observe_identity()
        leg = {
            "option_id": request.option_id,
            "side": request.side.value.lower(),
            "position_effect": request.position_effect,
            "ratio_quantity": 1,
        }
        arguments: dict[str, Any] = {
            "account_number": self._account,
            "legs": [leg],
            "type": request.order_type,
            "quantity": str(request.quantity),
            "time_in_force": request.time_in_force,
            "market_hours": request.market_hours,
        }
        if request.price is not None:
            arguments["price"] = str(request.price)
        if request.stop_price is not None:
            arguments["stop_price"] = str(request.stop_price)
        if request.chain_symbol is not None:
            arguments["chain_symbol"] = request.chain_symbol
            arguments["underlying_type"] = request.underlying_type
        payload = await self._gateway.review_option_order(arguments)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RobinhoodAgenticViolation("Robinhood Agentic option review is malformed")
        if (
            data.get("account_number") != self._account
            or data.get("type") != request.order_type
            or data.get("quantity") != str(request.quantity)
            or data.get("legs") != [leg]
        ):
            raise RobinhoodAgenticViolation(
                "Robinhood Agentic option review does not match the request"
            )
        checks_json, errors = _checks(data)
        quotes = data.get("option_quotes")
        if quotes is not None and not isinstance(quotes, list):
            raise RobinhoodAgenticViolation("Robinhood Agentic option quotes are malformed")
        fees = data.get("fees")
        if fees is not None and not isinstance(fees, dict):
            raise RobinhoodAgenticViolation("Robinhood Agentic option fees are malformed")
        total_fee = None if fees is None else _decimal(fees.get("total_fee"), "total fee")
        collateral = data.get("collateral")
        if collateral is not None:
            if (
                not isinstance(collateral, dict)
                or collateral.get("account_number") != self._account
            ):
                raise RobinhoodAgenticViolation(
                    "Robinhood Agentic option collateral identity does not match expected"
                )
        return RobinhoodAgenticOptionReviewEvidence(
            request=request,
            generated_at=self._clock(),
            identity_fingerprint=identity.fingerprint,
            order_checks_json=checks_json,
            quotes_json=canonical_json(quotes),
            total_fee=total_fee,
            collateral_json=canonical_json(collateral),
            errors=errors,
        )


__all__ = [
    "RobinhoodAgenticEquityReviewProvider",
    "RobinhoodAgenticOptionReviewProvider",
]
