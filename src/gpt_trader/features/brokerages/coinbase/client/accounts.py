"""Account and system endpoints for Coinbase REST client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from gpt_trader.features.brokerages.coinbase.errors import InvalidRequestError

if TYPE_CHECKING:
    from gpt_trader.features.brokerages.coinbase.client._typing import CoinbaseClientProtocol


class AccountClientMixin:
    """Methods related to accounts, system info, and limits."""

    def get_accounts(self: CoinbaseClientProtocol) -> dict[str, Any]:
        return self._request("GET", self._get_endpoint_path("accounts"))

    def list_all_accounts(self: CoinbaseClientProtocol) -> dict[str, Any]:
        endpoint = self._get_endpoint_path("accounts")
        accounts: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
            path = f"{endpoint}?limit=250"
            if cursor is not None:
                path = f"{path}&cursor={quote(cursor, safe='')}"
            response = self._request("GET", path)
            if not isinstance(response, dict) or not isinstance(response.get("accounts"), list):
                raise InvalidRequestError("Coinbase accounts response is malformed")
            page = response["accounts"]
            if any(not isinstance(row, dict) for row in page):
                raise InvalidRequestError("Coinbase accounts response is malformed")
            accounts.extend(page)

            if "has_next" not in response or type(response["has_next"]) is not bool:
                raise InvalidRequestError("Coinbase accounts pagination is malformed")
            has_next = response["has_next"]
            if not has_next:
                break

            next_cursor = response.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise InvalidRequestError("Coinbase accounts next cursor is missing")
            if next_cursor in seen_cursors:
                raise InvalidRequestError("Coinbase accounts cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return {"accounts": accounts}

    def get_account(self: CoinbaseClientProtocol, account_uuid: str) -> dict[str, Any]:
        path = self._get_endpoint_path(
            "account", account_uuid=account_uuid, account_id=account_uuid
        )
        return self._request("GET", path)

    def get_time(self: CoinbaseClientProtocol) -> dict[str, Any]:
        return self._request("GET", self._get_endpoint_path("time"))

    def get_key_permissions(self: CoinbaseClientProtocol) -> dict[str, Any]:
        path = self._get_endpoint_path("key_permissions")
        return self._request("GET", path)

    def get_fees(self: CoinbaseClientProtocol) -> dict[str, Any]:
        return self._request("GET", self._get_endpoint_path("fees"))

    def get_limits(self: CoinbaseClientProtocol) -> dict[str, Any]:
        path = self._get_endpoint_path("limits")
        return self._request("GET", path)

    def get_transaction_summary(self: CoinbaseClientProtocol) -> dict[str, Any]:
        if self.api_mode == "exchange":
            raise InvalidRequestError(
                "get_transaction_summary not available in exchange mode. "
                "Set COINBASE_API_MODE=advanced to use this feature."
            )
        path = self._get_endpoint_path("transaction_summary")
        return self._request("GET", path)


__all__ = ["AccountClientMixin"]
