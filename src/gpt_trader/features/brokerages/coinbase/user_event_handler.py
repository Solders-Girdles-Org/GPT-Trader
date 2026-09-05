"""Coinbase WebSocket user-event handler for order updates and fills."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from gpt_trader.features.brokerages.coinbase.rest.pnl_service import PnLService
from gpt_trader.features.brokerages.coinbase.rest.position_state_store import PositionStateStore
from gpt_trader.features.brokerages.coinbase.ws_events import FillEvent, OrderUpdateEvent
from gpt_trader.persistence.durability import WriteError
from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore, OrderStatus
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="coinbase_user_events")

_DEFAULT_DEDUPE_LIMIT = 1000
_DEFAULT_BACKFILL_ORDER_LIMIT = 100
_DEFAULT_BACKFILL_FILL_LIMIT = 200
_DEFAULT_BACKFILL_MIN_INTERVAL_SECONDS = 30.0
_EVENT_BACKFILL = "user_backfill"
_EVENT_BACKFILL_WATERMARK = "user_backfill_watermark"


class CoinbaseUserEventHandler:
    """Handle Coinbase user events and persist order/fill state."""

    def __init__(
        self,
        *,
        broker: Any | None,
        orders_store: OrdersStore | None,
        event_store: Any | None,
        bot_id: str,
        market_data_service: Any | None,
        symbols: list[str] | None,
        product_catalog: Any | None = None,
        rest_service: Any | None = None,
        dedupe_limit: int = _DEFAULT_DEDUPE_LIMIT,
    ) -> None:
        self._broker = broker
        self._orders_store = orders_store
        self._event_store = event_store
        self._bot_id = bot_id
        self._symbols = symbols or []
        self._market_data_service = market_data_service
        self._product_catalog = product_catalog
        self._rest_service = rest_service
        self._dedupe_limit = max(dedupe_limit, 1)

        self._pnl_service: PnLService | None = None
        if market_data_service is not None:
            self._pnl_service = PnLService(
                position_store=PositionStateStore(),
                market_data=market_data_service,
            )

        self._recent_fill_keys: deque[str] = deque()
        self._recent_fill_set: set[str] = set()
        self._dedupe_lock = threading.Lock()
        self._backfill_lock = threading.Lock()
        self._backfill_in_progress = False
        self._last_backfill_ts: float | None = None
        self._fill_watermark: datetime | None = None
        self._load_backfill_watermark()

    def handle_user_message(self, message: dict[str, Any]) -> None:
        """Parse and handle a raw WebSocket user message."""
        fill = FillEvent.from_message(message)
        if fill is not None:
            self.handle_fill(fill)

        updates = OrderUpdateEvent.from_message(message)
        for update in updates:
            self.handle_order_update(update)

    def handle_order_update(self, event: OrderUpdateEvent) -> None:
        """Handle order status updates from user events."""
        if self._orders_store is None:
            return

        order_id = event.order_id or event.client_order_id
        client_order_id = event.client_order_id or order_id
        if not order_id or not client_order_id:
            return

        timestamp = event.timestamp or datetime.now(timezone.utc)
        status = self._normalize_status(event.status)

        record = OrderRecord(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=event.product_id,
            side=event.side.lower() if event.side else "unknown",
            order_type=event.order_type.lower() if event.order_type else "unknown",
            quantity=event.size,
            price=event.price,
            status=status,
            filled_quantity=event.filled_size,
            average_fill_price=event.avg_price,
            created_at=timestamp,
            updated_at=timestamp,
            bot_id=self._bot_id,
            time_in_force="GTC",
            metadata={"source": "ws_user_event", "event_type": "order_update"},
        )

        try:
            result = self._orders_store.upsert_by_client_id(record, raise_on_error=True)
            if not result.success:
                raise WriteError(result.error or "Order update could not be persisted")
        except Exception as exc:
            logger.warning(
                "Failed to persist order update",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="order_update_persist",
                order_id=order_id,
            )
            return

        self._emit_event(
            "user_order_update",
            {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "symbol": event.product_id,
                "status": event.status,
                "filled_size": str(event.filled_size),
                "avg_price": str(event.avg_price) if event.avg_price else None,
            },
        )

    def handle_fill(self, event: FillEvent) -> None:
        """Handle fill events from user channel."""
        if not self._should_process_fill(event):
            return

        fill_payload = {
            "order_id": event.order_id,
            "client_order_id": event.client_order_id,
            "product_id": event.product_id,
            "side": event.side.lower() if event.side else None,
            "price": str(event.fill_price),
            "size": str(event.fill_size),
        }

        try:
            fill_delta = self._update_orders_for_fill(event)
        except Exception:
            self._forget_fill_key(self._fill_event_key(event))
            raise
        if fill_delta <= 0:
            return

        pnl_payload = dict(fill_payload)
        pnl_payload["size"] = str(fill_delta)

        self._process_fill_for_pnl(pnl_payload)

        self._emit_event(
            "user_fill",
            {
                **fill_payload,
                "fill_delta": str(fill_delta),
                "fee": str(event.fee),
                "commission": str(event.commission),
                "sequence": event.sequence,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            },
        )

    def _process_fill_for_pnl(self, fill: dict[str, Any]) -> None:
        if self._broker is not None and hasattr(self._broker, "process_fill_for_pnl"):
            try:
                self._broker.process_fill_for_pnl(fill)
                return
            except Exception as exc:
                logger.warning(
                    "Broker PnL fill processing failed",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="pnl_process_fill",
                )

        if self._pnl_service is None:
            return

        try:
            self._pnl_service.process_fill_for_pnl(fill)
        except Exception as exc:
            logger.warning(
                "PnL fill processing failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="pnl_process_fill",
            )

    def _update_orders_for_fill(self, event: FillEvent, *, cumulative: bool = True) -> Decimal:
        if self._orders_store is None:
            return event.fill_size

        order_id = event.order_id or event.client_order_id
        client_order_id = event.client_order_id or order_id
        if not order_id or not client_order_id:
            return Decimal("0")

        existing = self._orders_store.get_order_by_client_order_id(client_order_id)
        if existing is None:
            existing = self._orders_store.get_order(order_id)
            if existing is not None and not existing.checksum_is_valid():
                raise ValueError("Stored fill context failed checksum validation")

        fill_size = event.fill_size
        if fill_size <= 0:
            return Decimal("0")
        fill_price = event.fill_price

        if existing is None:
            quantity = fill_size
            filled_quantity = fill_size
            avg_price = fill_price
            fill_delta = fill_size
            status = OrderStatus.PARTIALLY_FILLED
            order_type = "unknown"
            price = None
            time_in_force = "GTC"
            created_at = event.timestamp or datetime.now(timezone.utc)
        else:
            quantity = existing.quantity
            previous_filled = existing.filled_quantity
            if cumulative:
                fill_delta = fill_size - previous_filled
                if fill_delta <= 0:
                    return Decimal("0")
                filled_quantity = fill_size
                avg_price = (
                    fill_price if fill_price > 0 else (existing.average_fill_price or fill_price)
                )
            else:
                fill_delta = fill_size
                if fill_delta <= 0:
                    return Decimal("0")
                filled_quantity = previous_filled + fill_size
                avg_price = self._merge_average_price(
                    existing.average_fill_price,
                    previous_filled,
                    fill_price,
                    fill_size,
                )
            status = (
                OrderStatus.FILLED
                if quantity > 0 and filled_quantity >= quantity
                else OrderStatus.PARTIALLY_FILLED
            )
            order_type = existing.order_type
            price = existing.price
            time_in_force = existing.time_in_force
            created_at = existing.created_at

        record = OrderRecord(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=event.product_id,
            side=event.side.lower() if event.side else "unknown",
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=status,
            filled_quantity=filled_quantity,
            average_fill_price=avg_price,
            created_at=created_at,
            updated_at=event.timestamp or datetime.now(timezone.utc),
            bot_id=self._bot_id,
            time_in_force=time_in_force,
            metadata={"source": "ws_user_event", "event_type": "fill"},
        )

        try:
            result = self._orders_store.upsert_by_client_id(record, raise_on_error=True)
            if not result.success:
                raise WriteError(result.error or "Order update could not be persisted")
        except Exception as exc:
            logger.warning(
                "Failed to persist fill update",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="fill_persist",
                order_id=order_id,
            )
            raise
        return fill_delta

    def request_backfill(self, *, reason: str, run_in_thread: bool = False) -> None:
        if run_in_thread:
            runner = threading.Thread(
                target=self.request_backfill,
                kwargs={"reason": reason},
                daemon=True,
            )
            runner.start()
            return

        if not self._acquire_backfill_slot():
            return

        try:
            self._run_backfill(reason)
        finally:
            self._release_backfill_slot()

    def _acquire_backfill_slot(self) -> bool:
        now = time.time()
        with self._backfill_lock:
            if self._backfill_in_progress:
                return False
            if (
                self._last_backfill_ts is not None
                and now - self._last_backfill_ts < _DEFAULT_BACKFILL_MIN_INTERVAL_SECONDS
            ):
                return False
            self._backfill_in_progress = True
            self._last_backfill_ts = now
            return True

    def _release_backfill_slot(self) -> None:
        with self._backfill_lock:
            self._backfill_in_progress = False

    def _run_backfill(self, reason: str) -> None:
        rest_service = self._get_rest_service()
        if rest_service is None:
            logger.debug(
                "Backfill skipped - no REST service available",
                operation="user_backfill",
                reason=reason,
            )
            return

        symbols = list(self._symbols)
        if not symbols:
            return

        orders_applied = 0
        fills_applied = 0
        latest_fill_ts = self._fill_watermark

        for symbol in symbols:
            try:
                orders = rest_service.list_orders(
                    product_id=symbol,
                    status=None,
                    limit=_DEFAULT_BACKFILL_ORDER_LIMIT,
                )
            except Exception as exc:
                logger.warning(
                    "REST order backfill failed",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="user_backfill",
                    stage="orders",
                    symbol=symbol,
                )
                orders = []

            for order in orders or []:
                if self._upsert_order_from_rest(order):
                    orders_applied += 1

            try:
                fills = rest_service.list_fills(
                    product_id=symbol,
                    order_id=None,
                    limit=_DEFAULT_BACKFILL_FILL_LIMIT,
                )
            except Exception as exc:
                logger.warning(
                    "REST fill backfill failed",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="user_backfill",
                    stage="fills",
                    symbol=symbol,
                )
                fills = []

            for fill in fills or []:
                applied, fill_ts = self._apply_rest_fill(fill)
                if applied:
                    fills_applied += 1
                if fill_ts and (latest_fill_ts is None or fill_ts > latest_fill_ts):
                    latest_fill_ts = fill_ts

        if latest_fill_ts is not None and (
            self._fill_watermark is None or latest_fill_ts > self._fill_watermark
        ):
            self._persist_backfill_watermark(latest_fill_ts, reason)

        self._emit_event(
            _EVENT_BACKFILL,
            {
                "reason": reason,
                "orders_applied": orders_applied,
                "fills_applied": fills_applied,
                "symbols": symbols,
            },
        )

    def _apply_rest_fill(self, fill: dict[str, Any]) -> tuple[bool, datetime | None]:
        if not self._should_process_rest_fill(fill):
            return False, None

        fill_ts = self._parse_fill_timestamp(fill)
        if self._fill_watermark is not None and fill_ts is not None:
            if fill_ts < self._fill_watermark:
                return False, fill_ts

        order_id = str(fill.get("order_id") or "")
        if not order_id:
            return False, fill_ts

        client_order_id = str(fill.get("client_order_id") or order_id)
        product_id = str(fill.get("product_id") or "")
        side = str(fill.get("side") or "")

        fill_price = self._coerce_decimal(fill.get("price"))
        fill_size = self._coerce_decimal(fill.get("size"))
        fee = self._coerce_decimal(fill.get("fee"))
        commission = self._coerce_decimal(fill.get("commission") or fill.get("total_fees"))

        event = FillEvent(
            order_id=order_id,
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            fill_price=fill_price,
            fill_size=fill_size,
            fee=fee,
            commission=commission,
            sequence=None,
            timestamp=fill_ts,
        )

        try:
            fill_delta = self._update_orders_for_fill(event, cumulative=False)
        except Exception:
            self._forget_fill_key(self._rest_fill_key(fill))
            raise
        if fill_delta <= 0:
            return False, fill_ts

        pnl_payload = {
            "order_id": order_id,
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": side.lower() if side else None,
            "price": str(fill_price),
            "size": str(fill_delta),
        }
        self._process_fill_for_pnl(pnl_payload)

        self._emit_event(
            "user_fill_backfill",
            {
                **pnl_payload,
                "fill_id": fill.get("fill_id"),
                "fee": str(fee),
                "commission": str(commission),
                "timestamp": fill_ts.isoformat() if fill_ts else None,
            },
        )
        return True, fill_ts

    def _upsert_order_from_rest(self, order: Any) -> bool:
        if self._orders_store is None:
            return False

        order_id = getattr(order, "id", None)
        if not order_id:
            return False

        client_order_id = getattr(order, "client_id", None) or order_id
        symbol = str(getattr(order, "symbol", "") or "")
        side = getattr(order, "side", None)
        order_type = getattr(order, "type", None)
        status = getattr(order, "status", None)
        quantity = self._coerce_decimal(getattr(order, "quantity", None))
        filled_quantity = self._coerce_decimal(getattr(order, "filled_quantity", None))
        avg_price = getattr(order, "avg_fill_price", None)
        price = getattr(order, "price", None)
        tif = getattr(order, "tif", None)

        created_at = (
            getattr(order, "created_at", None)
            or getattr(order, "submitted_at", None)
            or datetime.now(timezone.utc)
        )
        updated_at = getattr(order, "updated_at", None) or created_at

        record = OrderRecord(
            order_id=str(order_id),
            client_order_id=str(client_order_id),
            symbol=symbol,
            side=str(getattr(side, "value", side) or "").lower() or "unknown",
            order_type=str(getattr(order_type, "value", order_type) or "").lower() or "unknown",
            quantity=quantity,
            price=price,
            status=self._normalize_status(getattr(status, "value", status)),
            filled_quantity=filled_quantity,
            average_fill_price=avg_price,
            created_at=created_at,
            updated_at=updated_at,
            bot_id=self._bot_id,
            time_in_force=str(getattr(tif, "value", tif) or "GTC"),
            metadata={"source": "rest_backfill", "event_type": "order"},
        )

        try:
            result = self._orders_store.upsert_by_client_id(record, raise_on_error=True)
            if not result.success:
                raise WriteError(result.error or "Order update could not be persisted")
        except Exception as exc:
            logger.warning(
                "Failed to persist order backfill",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="user_backfill",
                stage="order_upsert",
                order_id=order_id,
            )
            return False
        return True

    def _parse_fill_timestamp(self, fill: dict[str, Any]) -> datetime | None:
        for key in ("trade_time", "created_at", "created_time", "timestamp", "time"):
            raw = fill.get(key)
            if not raw:
                continue
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
        return None

    def _coerce_decimal(self, value: Any, default: str = "0") -> Decimal:
        if value is None:
            return Decimal(default)
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal(default)

    def _get_rest_service(self) -> Any | None:
        if self._rest_service is not None:
            return self._rest_service
        if self._broker is None:
            return None

        list_orders = getattr(self._broker, "list_orders", None)
        list_fills = getattr(self._broker, "list_fills", None)
        if not callable(list_orders) or not callable(list_fills):
            return None

        try:
            from gpt_trader.features.brokerages.coinbase.endpoints import CoinbaseEndpoints
            from gpt_trader.features.brokerages.coinbase.market_data_service import (
                MarketDataService,
            )
            from gpt_trader.features.brokerages.coinbase.models import APIConfig
            from gpt_trader.features.brokerages.coinbase.rest_service import CoinbaseRestService
            from gpt_trader.features.brokerages.coinbase.utilities import ProductCatalog
            from gpt_trader.persistence.event_store import EventStore
        except Exception:
            return None

        auth = getattr(self._broker, "auth", None)
        key_name = getattr(auth, "key_name", None)
        private_key = getattr(auth, "private_key", None)
        base_url = getattr(self._broker, "base_url", "https://api.coinbase.com")
        api_mode_raw = getattr(self._broker, "api_mode", "advanced")
        api_mode: Literal["advanced", "exchange"] = (
            "exchange" if str(api_mode_raw).lower() == "exchange" else "advanced"
        )

        config = APIConfig(
            api_key=key_name or "",
            api_secret=private_key or "",
            passphrase=None,
            base_url=base_url,
            sandbox="sandbox" in str(base_url).lower(),
            cdp_api_key=key_name,
            cdp_private_key=private_key,
            api_mode=api_mode,
        )
        endpoints = CoinbaseEndpoints(config)
        product_catalog = self._product_catalog or ProductCatalog()
        market_data = self._market_data_service or MarketDataService(symbols=self._symbols)

        event_store = (
            self._event_store if isinstance(self._event_store, EventStore) else EventStore()
        )

        self._rest_service = CoinbaseRestService(
            client=self._broker,
            endpoints=endpoints,
            config=config,
            product_catalog=product_catalog,
            market_data=market_data,
            event_store=event_store,
            bot_config=None,
        )
        return self._rest_service

    def _load_backfill_watermark(self) -> None:
        if self._event_store is None:
            return

        getter = getattr(self._event_store, "get_recent_by_type", None)
        if not callable(getter):
            return

        try:
            events = getter(_EVENT_BACKFILL_WATERMARK, 1)
        except Exception:
            return

        if not events:
            return

        event = events[-1]
        if not isinstance(event, dict):
            return

        data = event.get("data")
        if not isinstance(data, dict):
            return

        timestamp = data.get("timestamp")
        if not timestamp:
            return

        try:
            self._fill_watermark = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            return

    def _persist_backfill_watermark(self, timestamp: datetime, reason: str) -> None:
        self._fill_watermark = timestamp
        self._emit_event(
            _EVENT_BACKFILL_WATERMARK,
            {"timestamp": timestamp.isoformat(), "reason": reason},
        )

    def _should_process_fill(self, event: FillEvent) -> bool:
        return self._record_fill_key(self._fill_event_key(event))

    def _fill_event_key(self, event: FillEvent) -> str:
        return self._build_fill_key(
            order_id=event.order_id,
            client_order_id=event.client_order_id,
            product_id=event.product_id,
            price=str(event.fill_price),
            size=str(event.fill_size),
            timestamp=event.timestamp.isoformat() if event.timestamp else None,
            sequence=event.sequence,
        )

    def _should_process_rest_fill(self, fill: dict[str, Any]) -> bool:
        return self._record_fill_key(self._rest_fill_key(fill))

    def _rest_fill_key(self, fill: dict[str, Any]) -> str:
        fill_id = fill.get("fill_id") or fill.get("trade_id")
        if fill_id:
            return f"fill_id:{fill_id}"

        return self._build_fill_key(
            order_id=str(fill.get("order_id") or ""),
            client_order_id=str(fill.get("client_order_id") or ""),
            product_id=str(fill.get("product_id") or ""),
            price=str(fill.get("price") or ""),
            size=str(fill.get("size") or ""),
            timestamp=str(fill.get("trade_time") or fill.get("created_at") or ""),
            sequence=None,
        )

    def _forget_fill_key(self, key: str) -> None:
        with self._dedupe_lock:
            self._recent_fill_set.discard(key)
            if key in self._recent_fill_keys:
                self._recent_fill_keys.remove(key)

    def _record_fill_key(self, key: str) -> bool:
        with self._dedupe_lock:
            if key in self._recent_fill_set:
                return False
            self._recent_fill_set.add(key)
            self._recent_fill_keys.append(key)
            while len(self._recent_fill_keys) > self._dedupe_limit:
                self._recent_fill_set.discard(self._recent_fill_keys.popleft())
        return True

    def _build_fill_key(
        self,
        *,
        order_id: str | None,
        client_order_id: str | None,
        product_id: str | None,
        price: str | None,
        size: str | None,
        timestamp: str | None,
        sequence: int | None,
    ) -> str:
        return "|".join(
            [
                order_id or "",
                client_order_id or "",
                product_id or "",
                price or "",
                size or "",
                timestamp or "",
                str(sequence or ""),
            ]
        )

    def _normalize_status(self, status: str | None) -> OrderStatus:
        normalized = str(status or "").strip().lower()
        mapping = {
            "pending": OrderStatus.PENDING,
            "open": OrderStatus.OPEN,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "cancelled": OrderStatus.CANCELLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "expired": OrderStatus.EXPIRED,
            "failed": OrderStatus.FAILED,
        }
        return mapping.get(normalized, OrderStatus.OPEN)

    def _merge_average_price(
        self,
        existing_avg: Decimal | None,
        existing_qty: Decimal,
        fill_price: Decimal,
        fill_qty: Decimal,
    ) -> Decimal:
        if existing_avg is None or existing_qty <= 0:
            return fill_price
        total_qty = existing_qty + fill_qty
        if total_qty <= 0:
            return fill_price
        total_cost = existing_avg * existing_qty + fill_price * fill_qty
        return total_cost / total_qty

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_store is None:
            return
        try:
            self._event_store.append(event_type, payload)
        except Exception as exc:
            logger.debug(
                "Failed to emit user event",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="user_event_emit",
                event_type=event_type,
            )
