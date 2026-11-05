import json
import logging

import pytest

from gpt_trader.logging.json_formatter import StructuredJSONFormatter


def test_json_redaction() -> None:
    formatter = StructuredJSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    record.extra = {
        "api_key": "secret_key_value",
        "apiKey": "camel_key",
        "privateKey": "camel_private",
        "nested": {"private_key": "secret_private_key", "public_data": "visible"},
        "list_data": [{"token": "secret_token"}, {"other": "visible"}],
    }

    json_output = formatter.format(record)
    data = json.loads(json_output)

    assert data["api_key"] == "[REDACTED]"
    assert data["apiKey"] == "[REDACTED]"
    assert data["privateKey"] == "[REDACTED]"
    assert data["nested"]["private_key"] == "[REDACTED]"
    assert data["nested"]["public_data"] == "visible"
    assert data["list_data"][0]["token"] == "[REDACTED]"
    assert data["list_data"][1]["other"] == "visible"


FAKE = "SEKRET-123"


def _format(extra: dict) -> dict:
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.extra = extra
    return json.loads(StructuredJSONFormatter().format(record))


@pytest.mark.parametrize(
    "key",
    [
        "api_secret",
        "coinbase_api_secret",
        "cdp_api_key_private",
        "private_key_pem",
        "refresh_token",
        "api-key",
        "X-API-Key",
        "CB-ACCESS-KEY",
        "CB-ACCESS-PASSPHRASE",
        "APCA-API-SECRET-KEY",
        "session_cookie",
        "db_password",
        "client_credential",
        "Proxy-Authorization",
    ],
)
def test_keys_containing_a_credential_fragment_are_redacted(key: str) -> None:
    data = _format({key: FAKE, "nested": {key: FAKE}})

    assert data[key] == "[REDACTED]"
    assert data["nested"][key] == "[REDACTED]"
    assert FAKE not in json.dumps(data)


@pytest.mark.parametrize("key", sorted(StructuredJSONFormatter.SENSITIVE_KEYS))
def test_exact_sensitive_names_stay_redacted_for_any_value(key: str) -> None:
    for value in (FAKE, True, None, 42):
        data = _format({key: value})
        assert data[key] == "[REDACTED]"


def test_header_pairs_in_lists_and_tuples_are_redacted() -> None:
    data = _format(
        {
            "headers": [
                ("Authorization", f"Bearer {FAKE}"),
                ("Content-Type", "application/json"),
                ["X-API-Key", FAKE],
            ],
            "header_tuple": (("Cookie", FAKE), ("Accept", "*/*")),
        }
    )

    assert data["headers"] == [
        ["Authorization", "[REDACTED]"],
        ["Content-Type", "application/json"],
        ["X-API-Key", "[REDACTED]"],
    ]
    assert data["header_tuple"] == [["Cookie", "[REDACTED]"], ["Accept", "*/*"]]
    assert FAKE not in json.dumps(data)


def test_dicts_inside_tuples_and_sets_are_redacted() -> None:
    data = _format(
        {
            "tuple_of_dicts": ({"password": FAKE, "user": "visible"},),
            "set_of_pairs": {("refresh_token", FAKE)},
            "frozen": frozenset({("api_secret", FAKE)}),
        }
    )

    assert data["tuple_of_dicts"] == [{"password": "[REDACTED]", "user": "visible"}]
    assert data["set_of_pairs"] == [["refresh_token", "[REDACTED]"]]
    assert data["frozen"] == [["api_secret", "[REDACTED]"]]
    assert FAKE not in json.dumps(data)


def test_tuples_keep_their_type_after_redaction() -> None:
    redacted = StructuredJSONFormatter()._redact_data(({"token": FAKE}, ("secret", FAKE)))

    assert redacted == ({"token": "[REDACTED]"}, ("secret", "[REDACTED]"))


def test_ordinary_fields_and_flags_stay_visible() -> None:
    data = _format(
        {
            "symbol": "BTC-USD",
            "keyword": "visible",
            "cdp_credentials_present": True,
            "private_key_loaded": False,
            "refresh_token_expiry": None,
            "pair": ("symbol", "ETH-USD"),
            7: "non-string key",
        }
    )

    assert data["symbol"] == "BTC-USD"
    assert data["keyword"] == "visible"
    assert data["cdp_credentials_present"] is True
    assert data["private_key_loaded"] is False
    assert data["refresh_token_expiry"] is None
    assert data["pair"] == ["symbol", "ETH-USD"]
    assert data["7"] == "non-string key"
