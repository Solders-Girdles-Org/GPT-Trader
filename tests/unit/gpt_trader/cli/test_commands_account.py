from __future__ import annotations

import argparse
import json
from argparse import Namespace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import gpt_trader.cli.commands.account as account_cmd
from gpt_trader.features.brokerages.accounts import (
    AccountIdentity,
    AccountObservation,
    AccountProvider,
    PreviewKind,
    PreviewRequest,
    PreviewResult,
)


def test_account_snapshot_inherits_parent_profile() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    account_cmd.register(subparsers)

    parent_profile_args = parser.parse_args(["account", "--profile", "prod", "snapshot"])
    assert parent_profile_args.profile == "prod"

    snapshot_profile_args = parser.parse_args(["account", "snapshot", "--profile", "prod"])
    assert snapshot_profile_args.profile == "prod"


def test_account_provider_commands_parse() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    account_cmd.register(subparsers)

    observe = parser.parse_args(["account", "observe", "--provider", "coinbase"])
    crypto_observe = parser.parse_args(["account", "observe", "--provider", "robinhood-crypto"])
    preview = parser.parse_args(
        [
            "account",
            "preview",
            "--provider",
            "coinbase",
            "--instrument",
            "BTC-USD",
            "--side",
            "buy",
            "--quantity",
            "0.01",
            "--order-type",
            "market",
        ]
    )

    assert observe.provider == "coinbase"
    assert crypto_observe.provider == "robinhood-crypto"
    assert preview.provider == "coinbase"
    assert preview.quantity == Decimal("0.01")

    option_preview = parser.parse_args(
        [
            "account",
            "preview",
            "--provider",
            "robinhood-agentic",
            "--product-type",
            "option",
            "--instrument",
            "option-id",
            "--side",
            "buy",
            "--position-effect",
            "open",
            "--quantity",
            "1",
            "--order-type",
            "limit",
            "--limit-price",
            "1.25",
        ]
    )
    assert option_preview.product_type == "option"
    assert option_preview.position_effect == "open"


def test_legacy_snapshot_rejects_provider_override() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    account_cmd.register(subparsers)

    with pytest.raises(SystemExit):
        parser.parse_args(["account", "snapshot", "--provider", "coinbase"])


def test_account_snapshot_prints_result(monkeypatch, capsys):
    captured: dict[str, object] = {}

    def fake_build_config(args, *, skip):
        captured["skip"] = set(skip)
        return "config"

    class SnapshotTelemetry:
        def supports_snapshots(self) -> bool:
            return True

        def collect_snapshot(self) -> dict[str, object]:
            return {"balance": 42}

    shutdown_called = {"count": 0}

    class StubBot:
        def __init__(self):
            self.account_telemetry = SnapshotTelemetry()

        async def shutdown(self):
            shutdown_called["count"] += 1

    monkeypatch.setattr(account_cmd.services, "build_config_from_args", fake_build_config)
    monkeypatch.setattr(
        account_cmd.services,
        "instantiate_bot",
        lambda config: StubBot(),
    )

    args = Namespace(profile="dev", account_command="snapshot")
    exit_code = account_cmd._handle_snapshot(args)

    assert exit_code == 0
    assert "account_command" in captured["skip"]
    out = capsys.readouterr().out
    assert json.loads(out)["balance"] == 42
    assert shutdown_called["count"] == 1


def test_account_snapshot_raises_when_unavailable(monkeypatch):
    class StubBot:
        """Bot without an account_telemetry attribute (broker-less container)."""

        async def shutdown(self):
            StubBot.shutdown_called = True

    StubBot.shutdown_called = False

    monkeypatch.setattr(
        account_cmd.services,
        "build_config_from_args",
        lambda *_, **__: "config",
    )
    monkeypatch.setattr(
        account_cmd.services,
        "instantiate_bot",
        lambda config: StubBot(),
    )

    with pytest.raises(RuntimeError):
        account_cmd._handle_snapshot(Namespace(profile="dev", account_command="snapshot"))

    assert StubBot.shutdown_called is True


def test_coinbase_observe_uses_attested_account_reader(monkeypatch):
    identity = AccountIdentity(
        provider=AccountProvider.COINBASE,
        account_id="portfolio-1",
        portfolio_id="portfolio-1",
        interface="advanced-trade-v3",
    )

    class Reader:
        def read_account(self) -> AccountObservation:
            return AccountObservation(
                identity=identity,
                generated_at=datetime(2026, 7, 12, tzinfo=UTC),
            )

    class Client:
        closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    access = SimpleNamespace(
        reader=Reader(),
        preview_provider=None,
        warnings=(),
        close=client.close,
    )
    monkeypatch.setattr(account_cmd, "_build_coinbase_access", lambda config, **_: access)

    response = account_cmd._handle_observe(
        Namespace(
            provider="coinbase",
            output_format="json",
            account_command="observe",
        )
    )

    assert response.success is True
    assert response.data["schema_version"] == "gpt-trader.account-observation.v1"
    assert response.data["identity"]["account_id"] == "*******io-1"
    assert client.closed is True


def test_coinbase_preview_command_returns_non_binding_result(monkeypatch):
    request_seen: list[PreviewRequest] = []

    class PreviewProvider:
        def preview(self, request: PreviewRequest) -> PreviewResult:
            request_seen.append(request)
            return PreviewResult(
                provider=AccountProvider.COINBASE,
                kind=PreviewKind.PROVIDER_SIMULATION,
                generated_at=datetime(2026, 7, 12, tzinfo=UTC),
                identity_fingerprint="fingerprint",
                request=request,
                estimated_total=Decimal("1000"),
            )

    class Client:
        closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    access = SimpleNamespace(
        reader=None,
        preview_provider=PreviewProvider(),
        warnings=(),
        close=client.close,
    )
    monkeypatch.setattr(account_cmd, "_build_coinbase_access", lambda config, **_: access)

    response = account_cmd._handle_preview(
        Namespace(
            provider="coinbase",
            output_format="json",
            instrument="BTC-USD",
            side="buy",
            quantity=Decimal("0.01"),
            order_type="market",
            limit_price=None,
        )
    )

    assert response.success is True
    assert response.data["non_binding"] is True
    assert response.data["estimated_total"] == "1000"
    assert request_seen[0].instrument == "BTC-USD"
    assert client.closed is True


def test_coinbase_access_uses_injected_composition_root() -> None:
    access = object()

    class Container:
        def create_coinbase_read_preview_access(self) -> object:
            return access

    assert account_cmd._build_coinbase_access("ignored", container=Container()) is access


def _observation(provider: AccountProvider) -> AccountObservation:
    return AccountObservation(
        identity=AccountIdentity(
            provider=provider,
            account_id="account-1234",
            portfolio_id="account-1234",
            interface="typed-read-review",
        ),
        generated_at=datetime(2026, 7, 13, tzinfo=UTC),
        buying_power={"cash": Decimal("100")},
    )


def test_robinhood_crypto_observe_and_diagnose_use_composition_root() -> None:
    closed = {"count": 0}

    class Reader:
        def read_account(self) -> AccountObservation:
            return _observation(AccountProvider.ROBINHOOD_CRYPTO)

    access = SimpleNamespace(
        reader=Reader(),
        warnings=("read only",),
        close=lambda: closed.__setitem__("count", closed["count"] + 1),
    )

    class Container:
        config = object()

        def create_robinhood_crypto_read_preview_access(self) -> object:
            return access

    observe = account_cmd._handle_observe(
        Namespace(provider="robinhood-crypto", application_container=Container())
    )
    diagnose = account_cmd._handle_diagnose(
        Namespace(provider="robinhood-crypto", application_container=Container())
    )

    assert observe.success is True
    assert observe.data["identity"]["provider"] == "robinhood_crypto"
    assert diagnose.success is True
    assert diagnose.data == {
        "provider": "robinhood_crypto",
        "status": "available",
        "identity": diagnose.data["identity"],
        "observation_schema_version": "gpt-trader.account-observation.v1",
        "balance_count": 0,
        "position_count": 0,
        "option_position_count": 0,
        "buying_power_dimensions": ["cash"],
    }
    assert diagnose.data["identity"]["account_id"] == "********1234"
    assert closed["count"] == 2


def test_robinhood_crypto_rejected_preview_is_non_binding_and_closes() -> None:
    closed = {"value": False}

    class PreviewProvider:
        def preview(self, request: PreviewRequest) -> PreviewResult:
            return PreviewResult(
                provider=AccountProvider.ROBINHOOD_CRYPTO,
                kind=PreviewKind.PROVIDER_ESTIMATE,
                generated_at=datetime(2026, 7, 13, tzinfo=UTC),
                identity_fingerprint="fingerprint",
                request=request,
                errors=("market requests only; no provider estimate was dispatched",),
            )

    access = SimpleNamespace(
        preview_provider=PreviewProvider(),
        close=lambda: closed.__setitem__("value", True),
    )

    class Container:
        config = object()

        def create_robinhood_crypto_read_preview_access(self) -> object:
            return access

    response = account_cmd._handle_preview(
        Namespace(
            provider="robinhood-crypto",
            product_type="equity",
            instrument="BTC-USD",
            side="buy",
            quantity=Decimal("0.01"),
            order_type="limit",
            limit_price=Decimal("100"),
            application_container=Container(),
        )
    )

    assert response.success is False
    details = response.errors[0].details
    assert details["provider_errors"] == [
        "market requests only; no provider estimate was dispatched"
    ]
    assert details["non_binding"] is True
    assert details["evidence"]["identity_fingerprint"] == "fingerprint"
    assert details["evidence"]["request"]["instrument"] == "BTC-USD"
    assert closed["value"] is True
