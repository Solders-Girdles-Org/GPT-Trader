"""Order persistence data models.

Pure data contracts for durable order state: the :class:`OrderStatus` lifecycle
enum and the :class:`OrderRecord` dataclass with its (de)serialization and
integrity helpers. The SQLite-backed store that reads and writes these records
lives in :mod:`gpt_trader.persistence.orders_store`; keeping the contracts here
lets callers depend on the order shape without importing the store machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from gpt_trader.persistence.durability import compute_checksum


class OrderStatus(str, Enum):
    """Order lifecycle states."""

    PENDING = "pending"  # Order submitted, awaiting confirmation
    OPEN = "open"  # Order accepted, waiting for fill
    PARTIALLY_FILLED = "partially_filled"  # Some fills received
    FILLED = "filled"  # Fully filled
    CANCELLED = "cancelled"  # Cancelled by user or system
    REJECTED = "rejected"  # Rejected by exchange
    EXPIRED = "expired"  # Time-in-force expired
    FAILED = "failed"  # Internal failure


@dataclass
class OrderRecord:
    """
    Persistent order record.

    Contains all order state needed for crash recovery.
    """

    order_id: str
    client_order_id: str
    symbol: str
    side: str  # buy | sell
    order_type: str  # market | limit | stop
    quantity: Decimal
    price: Decimal | None  # Limit price, None for market
    status: OrderStatus
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    created_at: datetime
    updated_at: datetime
    bot_id: str | None = None
    time_in_force: str = "GTC"
    metadata: dict[str, Any] | None = None
    checksum: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
            "status": self.status.value,
            "filled_quantity": str(self.filled_quantity),
            "average_fill_price": str(self.average_fill_price) if self.average_fill_price else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "bot_id": self.bot_id,
            "time_in_force": self.time_in_force,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderRecord:
        """Create from dictionary."""
        return cls(
            order_id=data["order_id"],
            client_order_id=data["client_order_id"],
            symbol=data["symbol"],
            side=data["side"],
            order_type=data["order_type"],
            quantity=Decimal(data["quantity"]),
            price=Decimal(data["price"]) if data.get("price") else None,
            status=OrderStatus(data["status"]),
            filled_quantity=Decimal(data["filled_quantity"]),
            average_fill_price=(
                Decimal(data["average_fill_price"]) if data.get("average_fill_price") else None
            ),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            bot_id=data.get("bot_id"),
            time_in_force=data.get("time_in_force", "GTC"),
            metadata=data.get("metadata"),
            checksum=data.get("checksum"),
        )

    def compute_checksum(self) -> str:
        """Compute checksum for order integrity verification."""
        version = (self.metadata or {}).get("record_checksum_version", 1)
        if type(version) is not int or version not in {1, 2}:
            raise ValueError("Unsupported order checksum version")
        if version == 2:
            # New admitted intents bind identity, lifecycle, fill facts, metadata
            # and timestamps. Historical rows retain their original checksum.
            return compute_checksum(self.to_dict())
        # Legacy checksum compatibility.
        critical = {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
        }
        return compute_checksum(critical)

    def checksum_is_valid(self) -> bool:
        metadata = self.metadata or {}
        version = metadata.get("record_checksum_version", 1)
        if type(version) is not int or version not in {1, 2}:
            return False
        if "intent" in metadata and version != 2:
            return False
        if not self.checksum:
            return version == 1
        return self.checksum == self.compute_checksum()

    def is_terminal(self) -> bool:
        """Check if order is in a terminal state."""
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        }
