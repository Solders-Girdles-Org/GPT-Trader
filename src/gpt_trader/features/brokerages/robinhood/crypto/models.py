"""Immutable provider-specific Robinhood Crypto evidence records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True, order=True)
class RobinhoodCryptoAccount:
    account_number: str
    status: str
    buying_power: Decimal
    buying_power_currency: str
    account_type: str
    is_api_tradable: bool
    fee_ratio: Decimal | None
    thirty_day_volume: Decimal | None
    next_fee_tier_ratio: Decimal | None
    next_fee_tier_threshold: Decimal | None


@dataclass(frozen=True, slots=True, order=True)
class RobinhoodCryptoHolding:
    account_number: str
    asset_code: str
    total_quantity: Decimal
    available_quantity: Decimal


@dataclass(frozen=True, slots=True, order=True)
class RobinhoodCryptoOrder:
    order_id: str
    account_number: str
    symbol: str
    client_order_id: str
    side: str
    order_type: str
    state: str
    average_price: Decimal | None
    filled_asset_quantity: Decimal
    fee_charged: Decimal
    estimated_fee_remaining: Decimal
    created_at: str
    updated_at: str
    executions_json: str
    configuration_json: str


@dataclass(frozen=True, slots=True, order=True)
class RobinhoodCryptoTradingPair:
    symbol: str
    asset_code: str
    quote_code: str
    asset_increment: Decimal
    quote_increment: Decimal
    max_order_size: Decimal
    min_order_amount: Decimal
    status: str
    is_api_tradable: bool


@dataclass(frozen=True, slots=True, order=True)
class RobinhoodCryptoQuote:
    symbol: str
    bid: Decimal
    ask: Decimal


@dataclass(frozen=True, slots=True, order=True)
class RobinhoodCryptoEstimate:
    symbol: str
    side: str
    quantity: Decimal
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    fee_ratio: Decimal
    estimated_fee: Decimal
    estimated_total_cost: Decimal
    estimated_total_credit: Decimal


__all__ = [
    "RobinhoodCryptoAccount",
    "RobinhoodCryptoEstimate",
    "RobinhoodCryptoHolding",
    "RobinhoodCryptoOrder",
    "RobinhoodCryptoQuote",
    "RobinhoodCryptoTradingPair",
]
