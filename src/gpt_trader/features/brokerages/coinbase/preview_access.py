"""Identity-bound Coinbase order-preview adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.accounts import (
    AccountProvider,
    PreviewKind,
    PreviewRequest,
    PreviewResult,
)
from gpt_trader.features.brokerages.coinbase.account_access import CoinbaseAccountReader
from gpt_trader.features.brokerages.coinbase.models import normalize_symbol


class CoinbasePreviewViolation(ValueError):
    """Raised when Coinbase preview evidence is malformed."""


def build_coinbase_preview_payload(request: PreviewRequest) -> dict[str, Any]:
    """Build the documented Advanced Trade preview payload without a client order ID."""
    configuration: dict[str, dict[str, Any]]
    if request.order_type is OrderType.MARKET:
        configuration = {"market_market_ioc": {"base_size": str(request.quantity)}}
    else:
        configuration = {
            "limit_limit_gtc": {
                "base_size": str(request.quantity),
                "limit_price": str(request.limit_price),
                "post_only": False,
            }
        }
    return {
        "product_id": normalize_symbol(request.instrument),
        "side": request.side.value,
        "order_configuration": configuration,
    }


class CoinbasePreviewClientProtocol(Protocol):
    """Narrow Coinbase client surface containing preview only."""

    def preview_order(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class CoinbasePreviewProvider:
    """Return non-binding Coinbase previews after account identity attestation."""

    def __init__(
        self,
        *,
        client: CoinbasePreviewClientProtocol,
        account_reader: CoinbaseAccountReader,
        clock: Callable[[], datetime],
    ) -> None:
        if not account_reader.uses_client(client):
            raise ValueError("preview client must match the attested account reader client")
        self._client = client
        self._account_reader = account_reader
        self._clock = clock

    def preview(self, request: PreviewRequest) -> PreviewResult:
        """Call only Coinbase's preview endpoint and normalize its evidence."""
        identity = self._account_reader.observe_identity()
        response = self._client.preview_order(build_coinbase_preview_payload(request))
        if not isinstance(response, dict):
            raise CoinbasePreviewViolation("Coinbase preview response must be an object")
        warnings = self._messages(response.get("warning"))
        errors = self._messages(response.get("errs"))
        estimate_fields = (
            "est_average_filled_price",
            "avg_filled_price",
            "best_ask",
            "best_bid",
            "commission_total",
            "order_total",
        )
        if not errors and not any(
            response.get(field) not in (None, "") for field in estimate_fields
        ):
            raise CoinbasePreviewViolation(
                "Coinbase preview response contains no recognized preview evidence"
            )

        price = self._optional_decimal(
            response.get("est_average_filled_price", response.get("avg_filled_price"))
        )
        if price is None:
            price_field = "best_ask" if request.side is OrderSide.BUY else "best_bid"
            price = self._optional_decimal(response.get(price_field))

        return PreviewResult(
            provider=AccountProvider.COINBASE,
            kind=PreviewKind.PROVIDER_SIMULATION,
            generated_at=self._clock(),
            identity_fingerprint=identity.fingerprint,
            request=request,
            estimated_price=price,
            estimated_fee=self._optional_decimal(response.get("commission_total")),
            estimated_total=self._optional_decimal(response.get("order_total")),
            warnings=warnings,
            errors=errors,
        )

    @staticmethod
    def _optional_decimal(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise CoinbasePreviewViolation("Coinbase preview decimal is malformed") from None
        if not parsed.is_finite():
            raise CoinbasePreviewViolation("Coinbase preview decimal must be finite")
        return parsed

    @staticmethod
    def _messages(value: Any) -> tuple[str, ...]:
        if value is None or value == "":
            return ()
        items = value if isinstance(value, list) else [value]
        messages: list[str] = []
        for item in items:
            if isinstance(item, dict):
                item = item.get("message") or item.get("error") or item.get("code")
                if item is None:
                    raise CoinbasePreviewViolation("Coinbase preview message entry is malformed")
            if item is not None and str(item):
                messages.append(str(item))
        return tuple(messages)


__all__ = [
    "CoinbasePreviewClientProtocol",
    "CoinbasePreviewProvider",
    "CoinbasePreviewViolation",
    "build_coinbase_preview_payload",
]
