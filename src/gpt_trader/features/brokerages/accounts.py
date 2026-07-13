"""Provider-neutral account observations and non-binding preview records."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.core.instruments import AssetClass, ProductType

ACCOUNT_OBSERVATION_SCHEMA_VERSION = "gpt-trader.account-observation.v1"


class AccountProvider(str, Enum):
    """Supported account-observation providers."""

    COINBASE = "coinbase"
    ROBINHOOD_CRYPTO = "robinhood_crypto"
    ROBINHOOD_AGENTIC = "robinhood_agentic"


class OptionRight(str, Enum):
    """Option contract right."""

    CALL = "call"
    PUT = "put"


class PreviewKind(str, Enum):
    """Strength of the provider response behind a preview."""

    PROVIDER_SIMULATION = "provider_simulation"
    PROVIDER_ESTIMATE = "provider_estimate"
    LOCAL_ESTIMATE = "local_estimate"


def _require_timezone(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _require_finite(value: Decimal | None, field_name: str) -> None:
    if value is not None and not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _mask_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    """Stable provider identity bound before account data is returned."""

    provider: AccountProvider
    account_id: str
    interface: str
    portfolio_id: str | None = None
    account_type: str = ""
    status: str = ""
    scope_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id is required")
        if not self.interface:
            raise ValueError("interface is required")

    @property
    def fingerprint(self) -> str:
        value = "\x1f".join(
            (
                self.provider.value,
                self.interface,
                self.account_id,
                self.portfolio_id or "",
                self.scope_fingerprint,
            )
        )
        return hashlib.sha256(value.encode()).hexdigest()

    def to_dict(self, *, show_identifiers: bool = False) -> dict[str, Any]:
        account_id = self.account_id if show_identifiers else _mask_identifier(self.account_id)
        portfolio_id = (
            self.portfolio_id if show_identifiers else _mask_identifier(self.portfolio_id)
        )
        return {
            "provider": self.provider.value,
            "interface": self.interface,
            "account_id": account_id,
            "portfolio_id": portfolio_id,
            "account_type": self.account_type,
            "status": self.status,
            "scope_fingerprint": self.scope_fingerprint or None,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ObservedBalance:
    """Balance reported by an account provider."""

    asset: str
    total: Decimal
    available: Decimal | None = None
    hold: Decimal | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("total", self.total),
            ("available", self.available),
            ("hold", self.hold),
        ):
            _require_finite(value, field_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "total": str(self.total),
            "available": _decimal(self.available),
            "hold": _decimal(self.hold),
        }


@dataclass(frozen=True, slots=True)
class ObservedPosition:
    """Crypto or equity position; negative quantity represents a short position."""

    instrument: str
    asset_class: AssetClass
    quantity: Decimal
    product_type: ProductType = ProductType.SPOT
    average_cost: Decimal | None = None
    market_value: Decimal | None = None

    def __post_init__(self) -> None:
        try:
            asset_class = (
                self.asset_class
                if isinstance(self.asset_class, AssetClass)
                else AssetClass(str(self.asset_class))
            )
        except ValueError:
            raise ValueError(f"unsupported asset class: {self.asset_class}") from None
        try:
            product_type = (
                self.product_type
                if isinstance(self.product_type, ProductType)
                else ProductType(str(self.product_type))
            )
        except ValueError:
            raise ValueError(f"unsupported product type: {self.product_type}") from None
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "product_type", product_type)
        for field_name, value in (
            ("quantity", self.quantity),
            ("average_cost", self.average_cost),
            ("market_value", self.market_value),
        ):
            _require_finite(value, field_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "asset_class": self.asset_class.value,
            "product_type": self.product_type.value,
            "quantity": str(self.quantity),
            "average_cost": _decimal(self.average_cost),
            "market_value": _decimal(self.market_value),
        }


@dataclass(frozen=True, slots=True)
class ObservedOptionPosition:
    """Structured option position without execution-risk inference."""

    contract_id: str
    underlying: str
    expiration: date
    strike: Decimal
    right: OptionRight
    multiplier: Decimal
    quantity: Decimal
    average_cost: Decimal | None = None
    market_price: Decimal | None = None

    def __post_init__(self) -> None:
        try:
            right = (
                self.right if isinstance(self.right, OptionRight) else OptionRight(str(self.right))
            )
        except ValueError:
            raise ValueError(f"unsupported option right: {self.right}") from None
        object.__setattr__(self, "right", right)
        for field_name, value in (
            ("strike", self.strike),
            ("multiplier", self.multiplier),
            ("quantity", self.quantity),
            ("average_cost", self.average_cost),
            ("market_price", self.market_price),
        ):
            _require_finite(value, field_name)
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "underlying": self.underlying,
            "expiration": self.expiration.isoformat(),
            "strike": str(self.strike),
            "right": self.right.value,
            "multiplier": str(self.multiplier),
            "quantity": str(self.quantity),
            "average_cost": _decimal(self.average_cost),
            "market_price": _decimal(self.market_price),
        }


@dataclass(frozen=True, slots=True)
class AccountObservation:
    """Versioned account snapshot retaining provider-specific asset shapes."""

    identity: AccountIdentity
    generated_at: datetime
    balances: tuple[ObservedBalance, ...] = ()
    positions: tuple[ObservedPosition, ...] = ()
    option_positions: tuple[ObservedOptionPosition, ...] = ()
    buying_power: Mapping[str, Decimal] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    source_metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_timezone(self.generated_at, "generated_at")
        object.__setattr__(self, "balances", tuple(self.balances))
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "option_positions", tuple(self.option_positions))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        for name, value in self.buying_power.items():
            _require_finite(value, f"buying_power.{name}")
        object.__setattr__(
            self,
            "buying_power",
            MappingProxyType(dict(self.buying_power)),
        )
        object.__setattr__(
            self,
            "source_metadata",
            MappingProxyType(dict(self.source_metadata)),
        )

    def to_dict(self, *, show_identifiers: bool = False) -> dict[str, Any]:
        return {
            "schema_version": ACCOUNT_OBSERVATION_SCHEMA_VERSION,
            "generated_at": self.generated_at.isoformat(),
            "identity": self.identity.to_dict(show_identifiers=show_identifiers),
            "balances": [item.to_dict() for item in self.balances],
            "positions": [item.to_dict() for item in self.positions],
            "option_positions": [item.to_dict() for item in self.option_positions],
            "buying_power": {name: str(value) for name, value in self.buying_power.items()},
            "warnings": list(self.warnings),
            "source_metadata": dict(self.source_metadata),
        }


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    """Normalized input for a non-binding preview or estimate."""

    instrument: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        instrument = self.instrument.strip().upper()
        if not instrument:
            raise ValueError("instrument is required")
        try:
            side = (
                self.side if isinstance(self.side, OrderSide) else OrderSide(str(self.side).upper())
            )
        except ValueError:
            raise ValueError(f"unsupported side: {self.side}") from None
        try:
            order_type = (
                self.order_type
                if isinstance(self.order_type, OrderType)
                else OrderType(str(self.order_type).upper())
            )
        except ValueError:
            raise ValueError(f"unsupported preview order type: {self.order_type}") from None
        if order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            raise ValueError(f"unsupported preview order type: {order_type.value}")

        _require_finite(self.quantity, "quantity")
        _require_finite(self.limit_price, "limit_price")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if order_type is OrderType.LIMIT:
            if self.limit_price is None:
                raise ValueError("limit price is required for limit preview")
            if self.limit_price <= 0:
                raise ValueError("limit price must be positive")
        elif self.limit_price is not None:
            raise ValueError("market preview cannot include a limit price")

        object.__setattr__(self, "instrument", instrument)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", order_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "side": self.side.value.lower(),
            "quantity": str(self.quantity),
            "order_type": self.order_type.value.lower(),
            "limit_price": _decimal(self.limit_price),
        }


@dataclass(frozen=True, slots=True)
class PreviewResult:
    """Non-binding provider simulation or price estimate."""

    provider: AccountProvider
    kind: PreviewKind
    generated_at: datetime
    identity_fingerprint: str
    request: PreviewRequest
    estimated_price: Decimal | None = None
    estimated_fee: Decimal | None = None
    estimated_total: Decimal | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_timezone(self.generated_at, "generated_at")
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "errors", tuple(self.errors))
        if not self.identity_fingerprint:
            raise ValueError("identity_fingerprint is required")
        for field_name, value in (
            ("estimated_price", self.estimated_price),
            ("estimated_fee", self.estimated_fee),
            ("estimated_total", self.estimated_total),
        ):
            _require_finite(value, field_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "kind": self.kind.value,
            "generated_at": self.generated_at.isoformat(),
            "identity_fingerprint": self.identity_fingerprint,
            "request": self.request.to_dict(),
            "estimated_price": _decimal(self.estimated_price),
            "estimated_fee": _decimal(self.estimated_fee),
            "estimated_total": _decimal(self.estimated_total),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "non_binding": True,
        }


__all__ = [
    "ACCOUNT_OBSERVATION_SCHEMA_VERSION",
    "AccountIdentity",
    "AccountObservation",
    "AccountProvider",
    "ObservedBalance",
    "ObservedOptionPosition",
    "ObservedPosition",
    "OptionRight",
    "PreviewKind",
    "PreviewRequest",
    "PreviewResult",
]
