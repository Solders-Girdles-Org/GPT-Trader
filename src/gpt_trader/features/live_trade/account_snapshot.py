"""Read-only account snapshot provider (account-snapshot ADR Option A).

Backs ``gpt-trader account snapshot`` — the runbooks' one-shot "what does the
broker say my account holds" verification step
(docs/decisions/account-snapshot-wire-or-remove.md). The service reads the
same broker surface the live engine reads (``StateCollector`` over
``list_balances`` / ``list_positions``, plus ticker marks), so the snapshot
can never diverge from what the engine would see. Strictly read-only: no
order authority, no state mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from gpt_trader.app.config import BotConfig
from gpt_trader.core import Balance, Position
from gpt_trader.core.protocols import ExtendedBrokerProtocol
from gpt_trader.features.live_trade.execution.state_collection import StateCollector
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="account_snapshot")


def _balance_payload(balance: Balance) -> dict[str, Any]:
    return {
        "asset": balance.asset,
        "total": str(balance.total),
        "available": str(balance.available),
        "hold": str(balance.hold),
    }


def _position_payload(position: Position, live_mark: str | None) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "side": position.side,
        "quantity": str(position.quantity),
        "entry_price": str(position.entry_price),
        "mark_price": str(position.mark_price),
        "live_mark": live_mark,
        "unrealized_pnl": str(position.unrealized_pnl),
        "realized_pnl": str(position.realized_pnl),
        "product_type": position.product_type,
    }


class AccountSnapshotService:
    """Collect a broker-truth account snapshot for operators.

    ``collect_snapshot`` raises whatever the broker raises for the core reads
    (balances/positions) — a snapshot that cannot reach the broker must fail
    loudly, not report zeros. Per-symbol ticker marks are best-effort and
    reported as ``null`` with the error noted, so one bad product cannot sink
    the whole verification step.
    """

    def __init__(self, broker: Any, config: BotConfig) -> None:
        self._broker = broker
        self._collector = StateCollector(cast(ExtendedBrokerProtocol, broker), config)

    def supports_snapshots(self) -> bool:
        return self._broker is not None

    def collect_snapshot(self) -> dict[str, Any]:
        (
            balances,
            equity,
            collateral_balances,
            collateral_total,
            positions,
        ) = self._collector.collect_account_state()

        marks: dict[str, str | None] = {}
        mark_errors: dict[str, str] = {}
        for symbol in sorted({position.symbol for position in positions}):
            try:
                ticker = self._broker.get_ticker(symbol) or {}
                raw_price = ticker.get("price")
                marks[symbol] = str(raw_price) if raw_price not in (None, "") else None
            except Exception as exc:
                marks[symbol] = None
                mark_errors[symbol] = f"{type(exc).__name__}: {exc}"
                logger.warning(f"Ticker mark unavailable for {symbol}: {exc}")

        unrealized_total = sum((position.unrealized_pnl for position in positions), Decimal("0"))

        payload: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "broker": type(self._broker).__name__,
            "balances": [_balance_payload(balance) for balance in balances],
            "collateral_available": str(equity),
            "collateral_total": str(collateral_total),
            "collateral_assets": sorted(balance.asset for balance in collateral_balances),
            "unrealized_pnl_total": str(unrealized_total),
            "equity": str(equity + unrealized_total),
            "positions": [
                _position_payload(position, marks.get(position.symbol)) for position in positions
            ],
            "marks": marks,
        }
        if mark_errors:
            payload["mark_errors"] = mark_errors
        return payload


__all__ = ["AccountSnapshotService"]
