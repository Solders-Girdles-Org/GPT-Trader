#!/usr/bin/env python3
"""Generate broker API documentation for AI agent consumption.

This script documents the broker interface protocols and methods,
including:
- Protocol definitions
- Method signatures and parameters
- Return types
- Example request/response patterns

Usage:
    python scripts/agents/generate_broker_api_docs.py [--output-dir DIR]

Output:
    var/agents/broker/
    - api_reference.json (protocol methods and signatures)
    - examples.json (request/response examples)
    - index.json (discovery file)
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


def get_method_info(method: Any, hints: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract method information including signature and docstring."""
    info: dict[str, Any] = {
        "docstring": inspect.getdoc(method) or "",
    }

    try:
        sig = inspect.signature(method)
        params = {}
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            param_info: dict[str, Any] = {}

            # Get type annotation
            if hints and name in hints:
                param_info["type"] = str(hints[name])
            elif param.annotation is not inspect.Parameter.empty:
                param_info["type"] = str(param.annotation)

            # Get default
            if param.default is not inspect.Parameter.empty:
                default = param.default
                if default is None or isinstance(default, (str, int, float, bool)):
                    param_info["default"] = default
                else:
                    param_info["default"] = str(default)
                param_info["required"] = False
            else:
                param_info["required"] = True

            params[name] = param_info

        info["parameters"] = params

        # Get return type
        if sig.return_annotation is not inspect.Parameter.empty:
            info["returns"] = str(sig.return_annotation)
        elif hints and "return" in hints:
            info["returns"] = str(hints["return"])

    except (ValueError, TypeError):
        pass

    return info


def generate_protocol_docs() -> dict[str, Any]:
    """Generate documentation for broker protocols."""
    from gpt_trader.core.protocols import (
        BrokerProtocol,
        ExtendedBrokerProtocol,
        MarketDataProtocol,
    )

    protocols = {}

    # Document BrokerProtocol
    broker_methods = {}
    for name, method in inspect.getmembers(BrokerProtocol, predicate=inspect.isfunction):
        if not name.startswith("_"):
            broker_methods[name] = get_method_info(method)

    protocols["BrokerProtocol"] = {
        "description": "Core protocol for broker implementations. Defines the minimal interface required for trading operations.",
        "implemented_by": ["CoinbaseRestService", "DeterministicBroker"],
        "methods": broker_methods,
    }

    # Document ExtendedBrokerProtocol
    extended_methods = {}
    for name, method in inspect.getmembers(ExtendedBrokerProtocol, predicate=inspect.isfunction):
        if not name.startswith("_") and name not in broker_methods:
            extended_methods[name] = get_method_info(method)

    protocols["ExtendedBrokerProtocol"] = {
        "description": "Extended broker protocol with additional methods for mark price tracking and position risk.",
        "extends": "BrokerProtocol",
        "additional_methods": extended_methods,
    }

    # Document MarketDataProtocol
    market_methods = {}
    for name, method in inspect.getmembers(MarketDataProtocol, predicate=inspect.isfunction):
        if not name.startswith("_"):
            market_methods[name] = get_method_info(method)

    protocols["MarketDataProtocol"] = {
        "description": "Protocol for market data streaming subscriptions.",
        "methods": market_methods,
    }

    return protocols


def generate_interface_examples() -> dict[str, Any]:
    """Generate example request/response patterns."""
    examples = {
        "get_product": {
            "description": "Get product metadata for a trading symbol",
            "request": {"symbol": "BTC-USD"},
            "response": {
                "symbol": "BTC-USD",
                "base_asset": "BTC",
                "quote_asset": "USD",
                "market_type": "SPOT",
                "min_size": "0.00001",
                "step_size": "0.00001",
                "min_notional": "1.00",
                "price_increment": "0.01",
                "leverage_max": None,
            },
        },
        "get_quote": {
            "description": "Get current bid/ask quote for a symbol",
            "request": {"symbol": "BTC-USD"},
            "response": {
                "symbol": "BTC-USD",
                "bid": "67500.00",
                "ask": "67510.00",
                "last": "67505.00",
                "ts": "2024-01-15T10:30:00Z",
            },
        },
        "list_positions": {
            "description": "List all current open positions",
            "request": {},
            "response": [
                {
                    "symbol": "BTC-USD",
                    "quantity": "0.5",
                    "entry_price": "67000.00",
                    "mark_price": "67500.00",
                    "unrealized_pnl": "250.00",
                    "realized_pnl": "0.00",
                    "side": "long",
                    "leverage": 1,
                }
            ],
        },
        "list_balances": {
            "description": "List all account balances",
            "request": {},
            "response": [
                {
                    "asset": "USD",
                    "total": "10000.00",
                    "available": "8500.00",
                    "hold": "1500.00",
                },
                {
                    "asset": "BTC",
                    "total": "0.5",
                    "available": "0.5",
                    "hold": "0.00",
                },
            ],
        },
        "place_order": {
            "description": "Place a trading order",
            "request": {
                "symbol": "BTC-USD",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": "0.1",
                "price": "67000.00",
                "tif": "GTC",
            },
            "response": {
                "id": "ord_123456789",
                "symbol": "BTC-USD",
                "side": "BUY",
                "type": "LIMIT",
                "quantity": "0.1",
                "status": "SUBMITTED",
                "filled_quantity": "0.0",
                "price": "67000.00",
                "tif": "GTC",
                "submitted_at": "2024-01-15T10:30:00Z",
            },
        },
        "cancel_order": {
            "description": "Cancel an existing order",
            "request": {"order_id": "ord_123456789"},
            "response": True,
        },
        "get_candles": {
            "description": "Get historical OHLCV candle data",
            "request": {
                "symbol": "BTC-USD",
                "granularity": "ONE_HOUR",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-02T00:00:00Z",
            },
            "response": [
                {
                    "ts": "2024-01-01T00:00:00Z",
                    "open": "67000.00",
                    "high": "67500.00",
                    "low": "66800.00",
                    "close": "67200.00",
                    "volume": "125.5",
                },
            ],
        },
    }

    return examples


def generate_error_scenarios() -> dict[str, Any]:
    """Document common error scenarios and handling."""
    return {
        "InvalidRequestError": {
            "description": "Request parameters are invalid",
            "causes": [
                "Invalid symbol format",
                "Quantity below minimum",
                "Price outside valid range",
            ],
            "handling": "Validate inputs before calling broker methods",
        },
        "InsufficientFunds": {
            "description": "Not enough balance to execute order",
            "causes": [
                "Available balance less than order value",
                "Funds on hold for other orders",
            ],
            "handling": "Check available balance before placing orders",
        },
        "NotFoundError": {
            "description": "Requested resource not found",
            "causes": [
                "Invalid order ID",
                "Unknown symbol",
            ],
            "handling": "Verify resource exists before operations",
        },
        "AuthError": {
            "description": "Authentication failed",
            "causes": [
                "Invalid API credentials",
                "Expired token",
                "Missing permissions",
            ],
            "handling": "Check credentials and permissions",
        },
        "RateLimitError": {
            "description": "API rate limit exceeded",
            "causes": [
                "Too many requests in time window",
            ],
            "handling": "Implement exponential backoff retry",
        },
    }


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate broker API documentation")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("var/agents/broker"),
        help="Output directory for documentation files",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Output to stdout instead of files",
    )

    args = parser.parse_args()

    print("Generating broker API documentation...")

    protocols = generate_protocol_docs()
    examples = generate_interface_examples()
    errors = generate_error_scenarios()

    api_reference = {
        "version": "1.0",
        "description": "Broker API reference for GPT-Trader",
        "protocols": protocols,
        "errors": errors,
    }

    examples_doc = {
        "version": "1.0",
        "description": "Request/response examples for broker API",
        "examples": examples,
    }

    if args.stdout:
        output = {
            "api_reference": api_reference,
            "examples": examples_doc,
        }
        print(json.dumps(output, indent=2, default=str))
        return 0

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write API reference
    api_path = output_dir / "api_reference.json"
    with open(api_path, "w") as f:
        json.dump(api_reference, f, indent=2, default=str)
    print(f"API reference written to: {api_path}")

    # Write examples
    examples_path = output_dir / "examples.json"
    with open(examples_path, "w") as f:
        json.dump(examples_doc, f, indent=2, default=str)
    print(f"Examples written to: {examples_path}")

    # Write index
    index = {
        "version": "1.0",
        "description": "Broker API documentation for AI agent consumption",
        "files": {
            "api_reference": "api_reference.json",
            "examples": "examples.json",
        },
        "protocols": list(protocols.keys()),
        "usage": {
            "get_broker": "broker = bot.broker",
            "check_protocol": "isinstance(broker, BrokerProtocol)",
            "place_order": "order = broker.place_order(symbol='BTC-USD', side=OrderSide.BUY, ...)",
        },
    }
    index_path = output_dir / "index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"Index written to: {index_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
