"""Lifecycle-managed Coinbase account observation and preview capability."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from gpt_trader.app.config import BotConfig
from gpt_trader.features.brokerages.coinbase.account_access import CoinbaseAccountReader
from gpt_trader.features.brokerages.coinbase.auth import SimpleAuth
from gpt_trader.features.brokerages.coinbase.client.read_preview import (
    CoinbaseReadPreviewClient,
)
from gpt_trader.features.brokerages.coinbase.credentials import resolve_coinbase_credentials
from gpt_trader.features.brokerages.coinbase.preview_access import CoinbasePreviewProvider


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, order=True)
class CoinbaseOrderEvidence:
    """Stable immutable evidence for one previously submitted Coinbase order."""

    order_id: str
    product_id: str
    side: str
    status: str
    filled_size: str
    filled_value: str
    outstanding_hold_amount: str
    order_configuration: str


@dataclass(frozen=True, slots=True)
class CoinbaseOrderHistoryEvidence:
    """Identity-bound immutable Coinbase order-history evidence."""

    identity_fingerprint: str
    orders: tuple[CoinbaseOrderEvidence, ...]


@dataclass(frozen=True, slots=True)
class CoinbaseReadPreviewAccess:
    """Own one narrow client and the adapters that share its attestation."""

    reader: CoinbaseAccountReader
    preview_provider: CoinbasePreviewProvider
    _close_client: Callable[[], None] = field(repr=False, compare=False)
    _list_orders: Callable[[], dict[str, Any]] = field(repr=False, compare=False)
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_config(
        cls,
        config: BotConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> CoinbaseReadPreviewAccess:
        portfolio_uuid = config.coinbase_expected_portfolio_uuid
        account_uuids = frozenset(config.coinbase_expected_account_uuids)
        if not portfolio_uuid:
            raise RuntimeError("COINBASE_EXPECTED_PORTFOLIO_UUID is required")
        if not account_uuids:
            raise RuntimeError("COINBASE_EXPECTED_ACCOUNT_UUIDS is required")
        if config.coinbase_sandbox_enabled:
            raise RuntimeError("real-account access requires COINBASE_SANDBOX=0")
        if config.coinbase_api_mode != "advanced":
            raise RuntimeError("real-account access requires COINBASE_API_MODE=advanced")

        credentials = resolve_coinbase_credentials()
        if credentials is None:
            raise RuntimeError(
                "Coinbase credentials not found. Set COINBASE_CREDENTIALS_FILE or "
                "COINBASE_CDP_API_KEY + COINBASE_CDP_PRIVATE_KEY."
            )

        client = CoinbaseReadPreviewClient(
            auth=SimpleAuth(
                key_name=credentials.key_name,
                private_key=credentials.private_key,
            ),
            api_mode="advanced",
            enable_response_cache=False,
        )
        try:
            reader = CoinbaseAccountReader(
                client=client,
                expected_portfolio_uuid=portfolio_uuid,
                expected_account_uuids=account_uuids,
                include_cfm=True,
                clock=clock,
            )
            preview_provider = CoinbasePreviewProvider(
                client=client,
                account_reader=reader,
                clock=clock,
            )
        except Exception:
            client.close()
            raise

        return cls(
            reader=reader,
            preview_provider=preview_provider,
            _close_client=client.close,
            _list_orders=client.list_all_orders,
            warnings=tuple(credentials.warnings),
        )

    def read_order_history(self) -> CoinbaseOrderHistoryEvidence:
        """Return typed order evidence after re-attesting the configured identity."""
        identity = self.reader.observe_identity()
        payload = self._list_orders()
        if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
            raise ValueError("Coinbase orders response is malformed")

        orders: list[CoinbaseOrderEvidence] = []
        for row in payload["orders"]:
            if not isinstance(row, dict):
                raise ValueError("Coinbase orders response is malformed")
            order_id = str(row.get("order_id") or "")
            if not order_id:
                raise ValueError("Coinbase order ID is missing")
            configuration = row.get("order_configuration")
            if not isinstance(configuration, dict):
                raise ValueError("Coinbase order configuration is malformed")
            try:
                configuration_json = json.dumps(
                    configuration,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("Coinbase order configuration is malformed") from exc
            orders.append(
                CoinbaseOrderEvidence(
                    order_id=order_id,
                    product_id=str(row.get("product_id") or ""),
                    side=str(row.get("side") or ""),
                    status=str(row.get("status") or ""),
                    filled_size=str(row.get("filled_size") or ""),
                    filled_value=str(row.get("filled_value") or ""),
                    outstanding_hold_amount=str(row.get("outstanding_hold_amount") or ""),
                    order_configuration=configuration_json,
                )
            )
        return CoinbaseOrderHistoryEvidence(
            identity_fingerprint=identity.fingerprint,
            orders=tuple(sorted(orders)),
        )

    def close(self) -> None:
        self._close_client()

    def __enter__(self) -> CoinbaseReadPreviewAccess:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "CoinbaseOrderEvidence",
    "CoinbaseOrderHistoryEvidence",
    "CoinbaseReadPreviewAccess",
]
