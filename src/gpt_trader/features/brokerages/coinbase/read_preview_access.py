"""Lifecycle-managed Coinbase account observation and preview capability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

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


@dataclass(frozen=True, slots=True)
class CoinbaseReadPreviewAccess:
    """Own one narrow client and the adapters that share its attestation."""

    client: CoinbaseReadPreviewClient
    reader: CoinbaseAccountReader
    preview_provider: CoinbasePreviewProvider
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
            client=client,
            reader=reader,
            preview_provider=preview_provider,
            warnings=tuple(credentials.warnings),
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> CoinbaseReadPreviewAccess:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["CoinbaseReadPreviewAccess"]
