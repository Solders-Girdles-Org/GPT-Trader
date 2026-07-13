"""Lifecycle-managed Robinhood Crypto read and estimate capability."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from gpt_trader.app.config import BotConfig
from gpt_trader.features.brokerages.robinhood.crypto.account_access import (
    RobinhoodCryptoAccountReader,
    RobinhoodCryptoViolation,
)
from gpt_trader.features.brokerages.robinhood.crypto.client import RobinhoodCryptoClient
from gpt_trader.features.brokerages.robinhood.crypto.credentials import (
    resolve_robinhood_crypto_credentials,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import (
    RobinhoodCryptoOrder,
    RobinhoodCryptoQuote,
    RobinhoodCryptoTradingPair,
)
from gpt_trader.features.brokerages.robinhood.crypto.preview_access import (
    RobinhoodCryptoPreviewProvider,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RobinhoodCryptoOrderEvidence:
    """Immutable order evidence without a raw account identifier."""

    order_id: str
    symbol: str
    client_order_id: str
    side: str
    order_type: str
    state: str
    average_price: str | None
    filled_asset_quantity: str
    fee_charged: str
    estimated_fee_remaining: str
    created_at: str
    updated_at: str
    executions_json: str
    configuration_json: str


@dataclass(frozen=True, slots=True)
class RobinhoodCryptoOrderHistoryEvidence:
    identity_fingerprint: str
    orders: tuple[RobinhoodCryptoOrderEvidence, ...]


@dataclass(frozen=True, slots=True)
class RobinhoodCryptoReadPreviewAccess:
    """Own one private GET-only client and typed adapters sharing its identity."""

    reader: RobinhoodCryptoAccountReader
    preview_provider: RobinhoodCryptoPreviewProvider
    _list_orders: Callable[[], tuple[RobinhoodCryptoOrder, ...]] = field(repr=False, compare=False)
    _list_trading_pairs: Callable[[Sequence[str]], tuple[RobinhoodCryptoTradingPair, ...]] = field(
        repr=False, compare=False
    )
    _get_quotes: Callable[[Sequence[str]], tuple[RobinhoodCryptoQuote, ...]] = field(
        repr=False, compare=False
    )
    _close_client: Callable[[], None] = field(repr=False, compare=False)

    @classmethod
    def from_config(
        cls,
        config: BotConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> RobinhoodCryptoReadPreviewAccess:
        expected_account_number = config.robinhood_crypto_expected_account_number
        if not expected_account_number:
            raise RuntimeError("ROBINHOOD_CRYPTO_EXPECTED_ACCOUNT_NUMBER is required")
        credentials = resolve_robinhood_crypto_credentials()
        if credentials is None:
            raise RuntimeError(
                "Robinhood Crypto credentials not found. Set "
                "ROBINHOOD_CRYPTO_API_KEY and ROBINHOOD_CRYPTO_PRIVATE_KEY."
            )
        client = RobinhoodCryptoClient(
            api_key=credentials.api_key,
            private_key=credentials.private_key,
            expected_account_number=expected_account_number,
        )
        try:
            reader = RobinhoodCryptoAccountReader(
                client=client,
                expected_account_number=expected_account_number,
                clock=clock,
            )
            preview = RobinhoodCryptoPreviewProvider(
                client=client,
                account_reader=reader,
                clock=clock,
            )
        except Exception:
            client.close()
            raise
        return cls(
            reader=reader,
            preview_provider=preview,
            _list_orders=client.list_orders,
            _list_trading_pairs=client.list_trading_pairs,
            _get_quotes=client.get_quotes,
            _close_client=client.close,
        )

    def read_order_history(self) -> RobinhoodCryptoOrderHistoryEvidence:
        identity = self.reader.observe_identity()
        orders = self._list_orders()
        if any(order.account_number != identity.account_id for order in orders):
            raise RobinhoodCryptoViolation("Robinhood Crypto order account does not match expected")
        return RobinhoodCryptoOrderHistoryEvidence(
            identity_fingerprint=identity.fingerprint,
            orders=tuple(
                RobinhoodCryptoOrderEvidence(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    client_order_id=order.client_order_id,
                    side=order.side,
                    order_type=order.order_type,
                    state=order.state,
                    average_price=(
                        None if order.average_price is None else str(order.average_price)
                    ),
                    filled_asset_quantity=str(order.filled_asset_quantity),
                    fee_charged=str(order.fee_charged),
                    estimated_fee_remaining=str(order.estimated_fee_remaining),
                    created_at=order.created_at,
                    updated_at=order.updated_at,
                    executions_json=order.executions_json,
                    configuration_json=order.configuration_json,
                )
                for order in orders
            ),
        )

    def list_trading_pairs(
        self, symbols: Sequence[str] = ()
    ) -> tuple[RobinhoodCryptoTradingPair, ...]:
        self.reader.observe_identity()
        return self._list_trading_pairs(symbols)

    def get_quotes(self, symbols: Sequence[str]) -> tuple[RobinhoodCryptoQuote, ...]:
        self.reader.observe_identity()
        return self._get_quotes(symbols)

    def close(self) -> None:
        self._close_client()

    def __enter__(self) -> RobinhoodCryptoReadPreviewAccess:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "RobinhoodCryptoOrderEvidence",
    "RobinhoodCryptoOrderHistoryEvidence",
    "RobinhoodCryptoReadPreviewAccess",
]
