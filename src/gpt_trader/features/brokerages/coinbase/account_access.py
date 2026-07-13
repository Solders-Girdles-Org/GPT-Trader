"""Identity-bound Coinbase account observation adapter."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from gpt_trader.core.instruments import AssetClass, ProductType
from gpt_trader.features.brokerages.accounts import (
    AccountIdentity,
    AccountObservation,
    AccountProvider,
    ObservedBalance,
    ObservedPosition,
)
from gpt_trader.features.brokerages.coinbase.errors import PermissionDeniedError


class CoinbaseAccountViolation(ValueError):
    """Raised when Coinbase account evidence violates the approved boundary."""


class CoinbaseObservationClientProtocol(Protocol):
    """Narrow Coinbase client surface needed for account observation."""

    def get_key_permissions(self) -> dict[str, Any]: ...

    def list_all_accounts(self) -> dict[str, Any]: ...

    def get_cfm_balance_summary(self) -> dict[str, Any]: ...

    def list_cfm_positions(self) -> dict[str, Any]: ...

    def get_product(self, product_id: str) -> dict[str, Any]: ...


class CoinbaseAccountReader:
    """Read a view-only Coinbase portfolio after exact identity attestation."""

    def __init__(
        self,
        *,
        client: CoinbaseObservationClientProtocol,
        expected_portfolio_uuid: str,
        expected_account_uuids: frozenset[str],
        include_cfm: bool,
        clock: Callable[[], datetime],
    ) -> None:
        if not expected_portfolio_uuid:
            raise ValueError("expected_portfolio_uuid is required")
        if not expected_account_uuids:
            raise ValueError("expected_account_uuids is required")
        self._client = client
        self._expected_portfolio_uuid = expected_portfolio_uuid
        self._expected_account_uuids = expected_account_uuids
        self._include_cfm = include_cfm
        self._clock = clock

    def uses_client(self, client: object) -> bool:
        """Return whether this reader and another adapter share one client."""
        return self._client is client

    def observe_identity(self) -> AccountIdentity:
        """Verify view-only permissions and exact account identity."""
        identity, _ = self._observe_identity_and_accounts()
        return identity

    def read_account(self) -> AccountObservation:
        """Return balances and enabled CFM positions from one attested account read."""
        identity, account_rows = self._observe_identity_and_accounts()
        balances = tuple(self._balance(row) for row in account_rows)
        positions: tuple[ObservedPosition, ...] = ()
        buying_power: dict[str, Decimal] = {}
        warnings: tuple[str, ...] = ()
        cfm_status = "not_requested"
        if self._include_cfm:
            try:
                cfm_balance, cfm_buying_power = self._cfm_balance(
                    self._client.get_cfm_balance_summary()
                )
                balances = (*balances, cfm_balance)
                buying_power["cfm_futures"] = cfm_buying_power
                positions = self._cfm_positions(self._client.list_cfm_positions())
                cfm_status = "complete"
            except PermissionDeniedError as exc:
                cfm_status = "unavailable"
                warnings = (f"CFM account data unavailable: {type(exc).__name__}",)

        return AccountObservation(
            identity=identity,
            generated_at=self._clock(),
            balances=balances,
            positions=positions,
            buying_power=buying_power,
            warnings=warnings,
            source_metadata={
                "interface": "advanced-trade-v3",
                "account_count": str(len(account_rows)),
                "cfm_included": str(self._include_cfm).lower(),
                "cfm_status": cfm_status,
            },
        )

    def _observe_identity_and_accounts(
        self,
    ) -> tuple[AccountIdentity, list[dict[str, Any]]]:
        permissions = self._client.get_key_permissions()
        if not isinstance(permissions, dict):
            raise CoinbaseAccountViolation("Coinbase permissions response is malformed")
        permission_fields = ("can_view", "can_trade", "can_transfer", "can_receive")
        if any(type(permissions.get(field_name)) is not bool for field_name in permission_fields):
            raise CoinbaseAccountViolation("Coinbase permissions response is malformed")
        if permissions["can_view"] is not True:
            raise CoinbaseAccountViolation("Coinbase view permission is required")
        if permissions.get("can_trade") is not False:
            raise CoinbaseAccountViolation("Coinbase trade permission must be disabled")
        if permissions.get("can_transfer") is not False:
            raise CoinbaseAccountViolation("Coinbase transfer permission must be disabled")
        if permissions.get("can_receive") is not False:
            raise CoinbaseAccountViolation("Coinbase receive permission must be disabled")

        portfolio_uuid = str(permissions.get("portfolio_uuid") or "")
        if portfolio_uuid != self._expected_portfolio_uuid:
            raise CoinbaseAccountViolation("Coinbase portfolio identity does not match expected")

        account_rows = self._account_rows(self._client.list_all_accounts())
        account_uuids = frozenset(str(row.get("uuid") or "") for row in account_rows)
        if "" in account_uuids:
            raise CoinbaseAccountViolation("Coinbase account UUID is missing")
        if len(account_uuids) != len(account_rows):
            raise CoinbaseAccountViolation("Coinbase accounts response contains duplicate UUIDs")
        if account_uuids != self._expected_account_uuids:
            raise CoinbaseAccountViolation("Coinbase account identities do not match expected")
        scope_fingerprint = hashlib.sha256("\x1f".join(sorted(account_uuids)).encode()).hexdigest()

        identity = AccountIdentity(
            provider=AccountProvider.COINBASE,
            account_id=portfolio_uuid,
            portfolio_id=portfolio_uuid,
            account_type=str(permissions.get("portfolio_type") or ""),
            status="observed",
            interface="advanced-trade-v3",
            scope_fingerprint=scope_fingerprint,
        )
        return identity, account_rows

    @staticmethod
    def _account_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or "accounts" not in payload:
            raise CoinbaseAccountViolation("Coinbase accounts response is malformed")
        rows = payload["accounts"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise CoinbaseAccountViolation("Coinbase accounts response is malformed")
        return rows

    @staticmethod
    def _amount(value: Any, *, field_name: str) -> Decimal:
        if isinstance(value, dict):
            if "value" not in value:
                raise CoinbaseAccountViolation(f"Coinbase {field_name} amount is missing")
            value = value["value"]
        if value is None or value == "":
            raise CoinbaseAccountViolation(f"Coinbase {field_name} amount is missing")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise CoinbaseAccountViolation(f"Coinbase {field_name} amount is malformed") from None
        if not amount.is_finite():
            raise CoinbaseAccountViolation(f"Coinbase {field_name} amount must be finite")
        return amount

    @classmethod
    def _balance(cls, row: dict[str, Any]) -> ObservedBalance:
        asset = str(row.get("currency") or "")
        if not asset:
            raise CoinbaseAccountViolation("Coinbase account currency is missing")
        available = cls._amount(row.get("available_balance"), field_name="available balance")
        hold = cls._amount(row.get("hold"), field_name="hold")
        total_value = row.get("balance", row.get("total_balance"))
        total = (
            cls._amount(total_value, field_name="total balance")
            if total_value is not None
            else available + hold
        )
        return ObservedBalance(
            asset=asset,
            total=total,
            available=available,
            hold=hold,
        )

    @classmethod
    def _cfm_balance(cls, payload: dict[str, Any]) -> tuple[ObservedBalance, Decimal]:
        if not isinstance(payload, dict) or not isinstance(payload.get("balance_summary"), dict):
            raise CoinbaseAccountViolation("Coinbase CFM balance response is malformed")
        summary = payload["balance_summary"]
        cfm_balance = cls._amount(summary.get("cfm_usd_balance"), field_name="CFM USD balance")
        available_margin = cls._amount(
            summary.get("available_margin"), field_name="CFM available margin"
        )
        open_order_hold = cls._amount(
            summary.get("total_open_orders_hold_amount"),
            field_name="CFM open-order hold",
        )
        futures_buying_power = cls._amount(
            summary.get("futures_buying_power"), field_name="CFM futures buying power"
        )
        return (
            ObservedBalance(
                asset="CFM_USD",
                total=cfm_balance,
                available=available_margin,
                hold=open_order_hold,
            ),
            futures_buying_power,
        )

    def _cfm_positions(self, payload: dict[str, Any]) -> tuple[ObservedPosition, ...]:
        if not isinstance(payload, dict) or "positions" not in payload:
            raise CoinbaseAccountViolation("Coinbase CFM positions response is malformed")
        rows = payload["positions"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise CoinbaseAccountViolation("Coinbase CFM positions response is malformed")
        positions: list[ObservedPosition] = []
        for row in rows:
            instrument = str(row.get("product_id") or row.get("symbol") or "")
            if not instrument:
                raise CoinbaseAccountViolation("Coinbase CFM position product is missing")

            quantity_field = next(
                (
                    field_name
                    for field_name in ("number_of_contracts", "net_size", "size")
                    if field_name in row
                ),
                None,
            )
            if quantity_field is None:
                raise CoinbaseAccountViolation("Coinbase CFM position quantity is missing")
            quantity = self._amount(row[quantity_field], field_name="CFM position quantity")
            if quantity == 0:
                continue
            asset_class = self._cfm_asset_class(self._client.get_product(instrument))

            side = str(row.get("side") or "").upper()
            if side == "LONG":
                quantity = abs(quantity)
            elif side == "SHORT":
                quantity = -abs(quantity)
            elif side:
                raise CoinbaseAccountViolation("Coinbase CFM position side is unsupported")
            elif quantity_field != "net_size":
                raise CoinbaseAccountViolation("Coinbase CFM position side is missing")
            average_cost_value = row.get("avg_entry_price", row.get("average_entry_price"))
            average_cost = (
                self._amount(average_cost_value, field_name="CFM average entry price")
                if average_cost_value is not None
                else None
            )
            positions.append(
                ObservedPosition(
                    instrument=instrument,
                    asset_class=asset_class,
                    product_type=ProductType.FUTURES,
                    quantity=quantity,
                    average_cost=average_cost,
                )
            )
        return tuple(positions)

    @staticmethod
    def _cfm_asset_class(payload: dict[str, Any]) -> AssetClass:
        if not isinstance(payload, dict):
            raise CoinbaseAccountViolation("Coinbase futures product response is malformed")
        details = payload.get("future_product_details")
        if not isinstance(details, dict) or type(details.get("non_crypto")) is not bool:
            raise CoinbaseAccountViolation("Coinbase futures product classification is missing")
        if details["non_crypto"]:
            raise CoinbaseAccountViolation(
                "Coinbase non-crypto futures are not supported by the account schema"
            )
        return AssetClass.CRYPTO


__all__ = [
    "CoinbaseAccountReader",
    "CoinbaseAccountViolation",
    "CoinbaseObservationClientProtocol",
]
