"""Order tooling commands for the CLI."""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

from gpt_trader.app.container import create_application_container
from gpt_trader.cli import options, services
from gpt_trader.cli.response import CliErrorCode, CliResponse
from gpt_trader.core import OrderSide, OrderType, TimeInForce
from gpt_trader.persistence.event_store import EventStore
from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore, OrderStatus

_CONFIG_SKIP_KEYS = {
    "orders_command",
    "order_id",
    "preview_id",
    "symbol",
    "side",
    "type",
    "quantity",
    "price",
    "stop",
    "tif",
    "client_id",
    "leverage",
    "reduce_only",
}

_DEFAULT_HISTORY_LIMIT = 20
_MAX_HISTORY_LIMIT = 200
_HISTORY_COMMAND_NAME = "orders history list"
_SUPPRESSED_COMMAND_NAME = "orders suppressed"
_SUPPRESSED_EVENT_TYPE = "order_suppressed"
T = TypeVar("T")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("orders", help="Order preview and edit tooling")
    options.add_profile_option(parser, allow_missing_default=True)
    orders_subparsers = parser.add_subparsers(dest="orders_command", required=True)

    preview = orders_subparsers.add_parser("preview", help="Preview a new order and exit")
    options.add_profile_option(preview, inherit_from_parent=True)
    options.add_order_arguments(preview)
    options.add_output_options(preview, include_quiet=False)
    preview.set_defaults(handler=_handle_preview, subcommand="preview")

    edit_preview = orders_subparsers.add_parser(
        "edit-preview", help="Preview edits for an existing order"
    )
    options.add_profile_option(edit_preview, inherit_from_parent=True)
    edit_preview.add_argument("--order-id", required=True, help="Order identifier to edit")
    options.add_order_arguments(edit_preview)
    options.add_output_options(edit_preview, include_quiet=False)
    edit_preview.set_defaults(handler=_handle_edit_preview, subcommand="edit-preview")

    apply_edit = orders_subparsers.add_parser(
        "apply-edit", help="Apply a previously previewed order edit"
    )
    options.add_profile_option(apply_edit, inherit_from_parent=True)
    apply_edit.add_argument("--order-id", required=True, help="Order identifier")
    apply_edit.add_argument("--preview-id", required=True, help="Preview identifier to apply")
    options.add_output_options(apply_edit, include_quiet=False)
    apply_edit.set_defaults(handler=_handle_apply_edit, subcommand="apply-edit")

    history = orders_subparsers.add_parser(
        "history", help="Inspect persisted order lifecycle records"
    )
    options.add_profile_option(history, inherit_from_parent=True)
    history_subparsers = history.add_subparsers(dest="history_command", required=True)

    history_list = history_subparsers.add_parser(
        "list", help="List recent persisted order lifecycle rows"
    )
    options.add_profile_option(history_list, inherit_from_parent=True)
    history_list.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_HISTORY_LIMIT,
        help=(
            "Maximum number of records to return (1-"
            f"{_MAX_HISTORY_LIMIT}, default {_DEFAULT_HISTORY_LIMIT})"
        ),
    )
    history_list.add_argument(
        "--symbol",
        type=str,
        help="Filter history by a trading symbol (exact match)",
    )
    history_list.add_argument(
        "--status",
        type=str,
        metavar="STATUS",
        help=(
            "Filter history by order status (pending, open, partially_filled, filled, "
            "cancelled, rejected, expired, failed)"
        ),
    )
    options.add_output_options(history_list, include_quiet=False)
    history_list.set_defaults(handler=_handle_history_list, subcommand="history list")

    suppressed = orders_subparsers.add_parser(
        "suppressed",
        help="Summarize dry-run suppressed order writes (order_suppressed events)",
    )
    options.add_profile_option(suppressed, inherit_from_parent=True)
    suppressed.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_HISTORY_LIMIT,
        help=(
            "Maximum number of events to return (1-"
            f"{_MAX_HISTORY_LIMIT}, default {_DEFAULT_HISTORY_LIMIT})"
        ),
    )
    options.add_output_options(suppressed, include_quiet=False)
    suppressed.set_defaults(handler=_handle_suppressed, subcommand="suppressed")


def _handle_preview(args: Namespace) -> CliResponse | int:
    output_format = getattr(args, "output_format", "text")
    command_name = "orders preview"

    try:
        config = services.build_config_from_args(args, skip=_CONFIG_SKIP_KEYS)
        bot = services.instantiate_bot(config)
    except Exception as e:
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.CONFIG_INVALID,
                message=f"Failed to initialize: {e}",
            )
        raise

    broker = bot.broker
    if not isinstance(broker, OrderPreviewBroker):
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.OPERATION_FAILED,
                message="Broker does not support order previews",
            )
        raise RuntimeError("Broker does not support order previews")

    preview_broker = cast(OrderPreviewBroker, broker)
    try:
        payload = _build_order_payload(args)
        result = preview_broker.preview_order(**payload)

        if output_format == "json":
            return CliResponse.success_response(
                command=command_name,
                data=result,
                was_noop=True,  # Preview is a no-op
            )

        print(json.dumps(result, indent=2, default=str))
        return 0
    finally:
        asyncio.run(bot.shutdown())


def _handle_edit_preview(args: Namespace) -> CliResponse | int:
    output_format = getattr(args, "output_format", "text")
    command_name = "orders edit-preview"

    try:
        config = services.build_config_from_args(args, skip=_CONFIG_SKIP_KEYS)
        bot = services.instantiate_bot(config)
    except Exception as e:
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.CONFIG_INVALID,
                message=f"Failed to initialize: {e}",
            )
        raise

    broker = bot.broker
    if not isinstance(broker, OrderPreviewBroker):
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.OPERATION_FAILED,
                message="Broker does not support order edit previews",
            )
        raise RuntimeError("Broker does not support order edit previews")

    preview_broker = cast(OrderPreviewBroker, broker)
    try:
        payload = _build_order_payload(args)
        result = preview_broker.edit_order_preview(order_id=args.order_id, **payload)

        if output_format == "json":
            return CliResponse.success_response(
                command=command_name,
                data=result,
                was_noop=True,
            )

        print(json.dumps(result, indent=2, default=str))
        return 0
    finally:
        asyncio.run(bot.shutdown())


def _handle_apply_edit(args: Namespace) -> CliResponse | int:
    output_format = getattr(args, "output_format", "text")
    command_name = "orders apply-edit"

    try:
        config = services.build_config_from_args(args, skip=_CONFIG_SKIP_KEYS)
        bot = services.instantiate_bot(config)
    except Exception as e:
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.CONFIG_INVALID,
                message=f"Failed to initialize: {e}",
            )
        raise

    broker = bot.broker
    if not isinstance(broker, OrderPreviewBroker):
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.OPERATION_FAILED,
                message="Broker does not support order edit application",
            )
        raise RuntimeError("Broker does not support order edit application")

    preview_broker = cast(OrderPreviewBroker, broker)
    try:
        order = preview_broker.edit_order(args.order_id, args.preview_id)
        data = asdict(order) if hasattr(order, "__dataclass_fields__") else order

        if output_format == "json":
            return CliResponse.success_response(
                command=command_name,
                data=data,
            )

        print(json.dumps(data, indent=2, default=str))
        return 0
    finally:
        asyncio.run(bot.shutdown())


def _handle_history_list(args: Namespace) -> CliResponse | int:
    output_format = getattr(args, "output_format", "text")
    command_name = _HISTORY_COMMAND_NAME

    try:
        limit = _validate_history_limit(args.limit)
    except ValueError as exc:
        message = str(exc)
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.INVALID_ARGUMENT,
                message=message,
                details={"limit": args.limit},
            )
        print(f"Error: {message}")
        return 1

    symbol = args.symbol.strip() if args.symbol else None
    if args.symbol is not None and not symbol:
        message = "Symbol filter cannot be empty"
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.INVALID_ARGUMENT,
                message=message,
                details={"symbol": args.symbol},
            )
        print(f"Error: {message}")
        return 1

    status_filter: OrderStatus | None = None
    if args.status:
        try:
            status_filter = _parse_status_filter(args.status)
        except ValueError as exc:
            message = str(exc)
            if output_format == "json":
                return CliResponse.error_response(
                    command=command_name,
                    code=CliErrorCode.INVALID_ARGUMENT,
                    message=message,
                    details={"status": args.status},
                )
            print(f"Error: {message}")
            return 1

    try:
        records = _with_orders_store(
            args,
            lambda store: store.list_orders(
                limit=limit,
                symbol=symbol,
                status=status_filter,
            ),
        )
    except Exception as exc:
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.OPERATION_FAILED,
                message="Failed to read order history",
                details={"error": str(exc)},
            )
        print(f"Error: Failed to read order history: {exc}")
        return 1

    if output_format == "json":
        return CliResponse.success_response(
            command=command_name,
            data={
                "orders": [record.to_dict() for record in records],
                "count": len(records),
                "filters": {
                    "limit": limit,
                    "symbol": symbol,
                    "status": status_filter.value if status_filter else None,
                },
            },
        )

    if not records:
        print("No order history records found.")
        return 0

    print(_format_history_text(records, limit, symbol, status_filter))
    return 0


def _handle_suppressed(args: Namespace) -> CliResponse | int:
    output_format = getattr(args, "output_format", "text")
    command_name = _SUPPRESSED_COMMAND_NAME

    try:
        limit = _validate_history_limit(args.limit)
    except ValueError as exc:
        message = str(exc)
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.INVALID_ARGUMENT,
                message=message,
                details={"limit": args.limit},
            )
        print(f"Error: {message}")
        return 1

    try:
        events = _with_event_store(
            args,
            lambda store: store.get_recent_by_type(_SUPPRESSED_EVENT_TYPE, limit),
        )
    except Exception as exc:
        if output_format == "json":
            return CliResponse.error_response(
                command=command_name,
                code=CliErrorCode.OPERATION_FAILED,
                message="Failed to read suppressed-order events",
                details={"error": str(exc)},
            )
        print(f"Error: Failed to read suppressed-order events: {exc}")
        return 1

    rows = [_suppressed_row(event) for event in reversed(events)]
    counts_by_action: dict[str, int] = {}
    for row in rows:
        action = cast(str, row["action"])
        counts_by_action[action] = counts_by_action.get(action, 0) + 1

    if output_format == "json":
        return CliResponse.success_response(
            command=command_name,
            data={
                "events": rows,
                "count": len(rows),
                "counts_by_action": counts_by_action,
                "filters": {"limit": limit},
            },
        )

    if not rows:
        print("No suppressed order writes recorded.")
        return 0

    print(_format_suppressed_text(rows, counts_by_action, limit))
    return 0


def _suppressed_row(event: Any) -> dict[str, Any]:
    data = event.get("data") if isinstance(event, Mapping) else None
    if not isinstance(data, Mapping):
        data = {}
    timestamp = data.get("timestamp")
    recorded_at = (
        datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
        if isinstance(timestamp, (int, float))
        else None
    )
    return {
        "recorded_at": recorded_at,
        "action": str(data.get("action") or "unknown"),
        "symbol": data.get("symbol"),
        "side": data.get("side"),
        "quantity": data.get("quantity"),
        "price": data.get("price"),
        "order_id": data.get("order_id"),
        "reason": data.get("reason"),
        "bot_id": data.get("bot_id"),
    }


def _format_suppressed_text(
    rows: list[dict[str, Any]],
    counts_by_action: dict[str, int],
    limit: int,
) -> str:
    count = len(rows)
    counts_line = ", ".join(
        f"{action}={counts_by_action[action]}" for action in sorted(counts_by_action)
    )
    lines = [
        f"Suppressed order writes (limit={limit})",
        f"Returned {count} event{'s' if count != 1 else ''} (newest first)",
        f"Counts by action: {counts_line}",
        "",
    ]
    for row in rows:
        detail_parts = [
            f"{key}={row[key]}"
            for key in ("symbol", "side", "quantity", "price", "order_id", "bot_id")
            if row.get(key) is not None
        ]
        recorded = row["recorded_at"] or "unknown-time"
        reason = row["reason"] or "unknown"
        detail = f" {' '.join(detail_parts)}" if detail_parts else ""
        lines.append(f"{recorded} {row['action']} (reason={reason}){detail}")
    return "\n".join(lines)


def _with_event_store(args: Namespace, callback: Callable[[EventStore], T]) -> T:
    config = services.build_config_from_args(args, skip=_CONFIG_SKIP_KEYS)
    container = create_application_container(config)
    store = container.event_store
    try:
        return callback(store)
    finally:
        store.close()


def _validate_history_limit(limit: int) -> int:
    if limit < 1:
        raise ValueError("Limit must be at least 1")
    if limit > _MAX_HISTORY_LIMIT:
        raise ValueError(f"Limit must not exceed {_MAX_HISTORY_LIMIT}")
    return limit


def _parse_status_filter(raw: str) -> OrderStatus:
    normalized = raw.strip()
    if not normalized:
        raise ValueError("Status filter cannot be empty")

    try:
        return OrderStatus[normalized.upper()]
    except KeyError:
        pass

    try:
        return OrderStatus(normalized.lower())
    except ValueError:
        raise ValueError(f"Unknown order status '{raw}'")


def _format_history_text(
    records: list[OrderRecord],
    limit: int,
    symbol_filter: str | None,
    status_filter: OrderStatus | None,
) -> str:
    count = len(records)
    filters_line = (
        f"Filters: symbol={symbol_filter or 'any'}, "
        f"status={status_filter.value if status_filter else 'any'}"
    )
    header = f"Order history (limit={limit})"
    summary = f"Returned {count} record{'s' if count != 1 else ''}"

    column_names = [
        "Order ID",
        "Client ID",
        "Symbol",
        "Status",
        "Quantity",
        "Filled",
        "Price",
        "Updated At",
    ]

    rows: list[list[str]] = []
    for record in records:
        rows.append(
            [
                record.order_id,
                record.client_order_id or "-",
                record.symbol,
                record.status.value,
                str(record.quantity),
                str(record.filled_quantity),
                str(record.price) if record.price is not None else "-",
                record.updated_at.isoformat(),
            ]
        )

    widths = [
        max(len(column), max(len(row[idx]) for row in rows))
        for idx, column in enumerate(column_names)
    ]

    header_row = " | ".join(column.ljust(widths[idx]) for idx, column in enumerate(column_names))
    divider = "-+-".join("-" * width for width in widths)

    lines = [
        header,
        filters_line,
        summary,
        "",
        header_row,
        divider,
    ]
    lines.extend(
        " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)) for row in rows
    )

    return "\n".join(lines)


def _with_orders_store(args: Namespace, callback: Callable[[OrdersStore], T]) -> T:
    config = services.build_config_from_args(args, skip=_CONFIG_SKIP_KEYS)
    container = create_application_container(config)
    store = container.orders_store
    try:
        return callback(store)
    finally:
        store.close()


def _build_order_payload(args: Namespace) -> dict[str, object]:
    side = OrderSide[args.side.upper()]
    order_type = OrderType[args.type.upper()]
    quantity = Decimal(str(args.quantity))
    tif = TimeInForce[args.tif.upper()] if args.tif else TimeInForce.GTC

    price = Decimal(str(args.price)) if args.price is not None else None
    stop = Decimal(str(args.stop)) if args.stop is not None else None

    payload: dict[str, object] = {
        "symbol": args.symbol,
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        "tif": tif,
        "reduce_only": bool(args.reduce_only),
        "leverage": args.leverage,
        "client_id": args.client_id,
    }

    if price is not None:
        payload["price"] = price
    if stop is not None:
        payload["stop_price"] = stop

    return payload


@runtime_checkable
class OrderPreviewBroker(Protocol):
    def preview_order(self, **kwargs: Any) -> Any: ...

    def edit_order_preview(self, order_id: str, **kwargs: Any) -> Any: ...

    def edit_order(self, order_id: str, preview_id: str, **kwargs: Any) -> Any: ...
