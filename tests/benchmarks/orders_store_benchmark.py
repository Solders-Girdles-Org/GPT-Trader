import os
import sys
import time
from decimal import Decimal
from tempfile import TemporaryDirectory

# Add src to python path for imports
sys.path.insert(0, os.path.abspath("src"))

from datetime import UTC, datetime

from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore, OrderStatus


def create_test_order(order_id):
    now = datetime.now(UTC)
    return OrderRecord(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        symbol="BTC-USD",
        side="buy",
        order_type="market",
        quantity=Decimal("1.5"),
        price=Decimal("50000"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("1.5"),
        average_fill_price=Decimal("50000"),
        created_at=now,
        updated_at=now,
        bot_id="test-bot",
        time_in_force="GTC",
        metadata=None,
    )


def run_benchmark():
    num_orders = 50000
    print(f"Setting up benchmark with {num_orders} orders...")

    with TemporaryDirectory() as tmpdir:
        with OrdersStore(tmpdir) as store:
            for i in range(num_orders):
                order = create_test_order(f"order_{i}")
                store.save_order(order)

            print("Running verify_integrity...")
            start_time = time.time()
            valid_count, invalid_orders = store.verify_integrity()
            end_time = time.time()

            duration = end_time - start_time
            print(f"Verified {valid_count} orders in {duration:.4f} seconds.")
            print(f"Speed: {valid_count / duration:.2f} orders/sec")
            return duration


if __name__ == "__main__":
    run_benchmark()
