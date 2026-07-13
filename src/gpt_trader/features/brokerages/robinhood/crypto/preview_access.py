"""Identity-bound Robinhood Crypto estimated-price previews."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.accounts import (
    AccountProvider,
    PreviewKind,
    PreviewRequest,
    PreviewResult,
)
from gpt_trader.features.brokerages.robinhood.crypto.account_access import (
    RobinhoodCryptoAccountReader,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import RobinhoodCryptoEstimate


class RobinhoodCryptoEstimateClientProtocol(Protocol):
    def get_estimated_price(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
    ) -> RobinhoodCryptoEstimate: ...


class RobinhoodCryptoPreviewProvider:
    """Return a non-binding market estimate after identity attestation."""

    def __init__(
        self,
        *,
        client: RobinhoodCryptoEstimateClientProtocol,
        account_reader: RobinhoodCryptoAccountReader,
        clock: Callable[[], datetime],
    ) -> None:
        if not account_reader.uses_client(client):
            raise ValueError("estimate client must match the attested account reader client")
        self._client = client
        self._account_reader = account_reader
        self._clock = clock

    def preview(self, request: PreviewRequest) -> PreviewResult:
        identity = self._account_reader.observe_identity()
        if request.order_type is not OrderType.MARKET:
            return PreviewResult(
                provider=AccountProvider.ROBINHOOD_CRYPTO,
                kind=PreviewKind.PROVIDER_ESTIMATE,
                generated_at=self._clock(),
                identity_fingerprint=identity.fingerprint,
                request=request,
                errors=(
                    "Robinhood Crypto estimated price supports market requests only; "
                    "no provider estimate was dispatched",
                ),
            )

        side = "ask" if request.side is OrderSide.BUY else "bid"
        estimate = self._client.get_estimated_price(
            symbol=request.instrument,
            side=side,
            quantity=request.quantity,
        )
        price = estimate.ask if request.side is OrderSide.BUY else estimate.bid
        total = (
            estimate.estimated_total_cost
            if request.side is OrderSide.BUY
            else estimate.estimated_total_credit
        )
        return PreviewResult(
            provider=AccountProvider.ROBINHOOD_CRYPTO,
            kind=PreviewKind.PROVIDER_ESTIMATE,
            generated_at=self._clock(),
            identity_fingerprint=identity.fingerprint,
            request=request,
            estimated_price=price,
            estimated_fee=estimate.estimated_fee,
            estimated_total=total,
            warnings=(
                "Provider estimate is non-binding and does not validate or reserve an order",
            ),
        )


__all__ = [
    "RobinhoodCryptoEstimateClientProtocol",
    "RobinhoodCryptoPreviewProvider",
]
