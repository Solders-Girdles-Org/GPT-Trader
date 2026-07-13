from __future__ import annotations

import pytest

from gpt_trader.features.brokerages.robinhood.crypto.client import (
    ROBINHOOD_CRYPTO_BASE_URL,
    RobinhoodCryptoClientViolation,
)
from tests.unit.gpt_trader.features.brokerages.robinhood.crypto.client_test_support import (
    ACCOUNT,
    Response,
    Session,
    account_row,
    client,
    order_row,
    page,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [("side", "transfer"), ("state", "mutation_pending"), ("type", "convert")],
)
def test_rejects_order_enum_drift(field: str, value: str) -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/orders/?account_number={ACCOUNT}"
    row = order_row()
    row[field] = value

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(Session([Response(url, page([row]))])).list_orders()


def test_rejects_account_status_drift() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    row = account_row()
    row["status"] = "mutation_enabled"

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(Session([Response(url, page([row]))])).list_accounts()


def test_rejects_request_only_market_config_field_in_order_response() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/orders/?account_number={ACCOUNT}"
    row = order_row()
    row["type"] = "market"
    row.pop("limit_order_config")
    row["market_order_config"] = {"asset_quantity": "1", "quote_amount": "10"}

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(Session([Response(url, page([row]))])).list_orders()
