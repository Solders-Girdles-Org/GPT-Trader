"""CLI tests for gpt_trader.cli."""

from __future__ import annotations

import builtins
import importlib
from types import SimpleNamespace

import pytest


def test_cli_root_help_shows_command_list(capsys):
    cli = importlib.import_module("gpt_trader.cli")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "Coinbase Trading Bot" in output
    for command_name in (
        "run",
        "account",
        "coinbase",
        "controls",
        "orders",
        "report",
        "strategy",
        "preflight",
        "broker-check",
    ):
        assert command_name in output
    assert "Perpetuals Trading Bot" not in output


def test_cli_run_defaults_to_run_subcommand(monkeypatch):
    argv = [
        "--profile",
        "dev",
        "--dry-run",
        "--symbols",
        "BTC-PERP",
        "ETH-PERP",
        "--interval",
        "3",
        "--leverage",
        "2",
        "--reduce-only",
        "--dev-fast",
    ]

    captured = {}

    class DummyConfig:
        def __init__(self, profile: str):
            self.profile = SimpleNamespace(value=profile)

    class DummyBot:
        def __init__(self):
            self.running = True
            self.single_cycle = None

        async def run(self, *, single_cycle: bool = False):
            self.single_cycle = single_cycle

    cli = importlib.import_module("gpt_trader.cli")
    services = importlib.import_module("gpt_trader.cli.services")

    def fake_build_config(args, *, include=None, skip=None):
        captured["profile"] = args.profile
        captured["symbols"] = list(args.symbols)
        captured["interval"] = args.interval
        captured["target_leverage"] = args.target_leverage
        captured["include"] = set(include or [])
        captured["skip"] = set(skip or [])
        return DummyConfig(args.profile)

    dummy_bot = DummyBot()

    def fake_instantiate(config):
        captured["config"] = config
        return dummy_bot

    monkeypatch.setattr(services, "build_config_from_args", fake_build_config)
    monkeypatch.setattr(services, "instantiate_bot", fake_instantiate)

    exit_code = cli.main(argv)

    assert exit_code == 0
    assert captured["profile"] == "dev"
    assert captured["symbols"] == ["BTC-PERP", "ETH-PERP"]
    assert captured["interval"] == 3
    assert captured["target_leverage"] == 2
    assert "dev_fast" in captured["skip"]
    assert dummy_bot.single_cycle is True


def test_orders_preview_invokes_broker_preview(monkeypatch):
    argv = [
        "orders",
        "preview",
        "--profile",
        "dev",
        "--symbol",
        "BTC-PERP",
        "--side",
        "buy",
        "--type",
        "limit",
        "--quantity",
        "0.50",
        "--price",
        "42000",
        "--tif",
        "IOC",
        "--client-id",
        "cli-123",
        "--leverage",
        "3",
        "--reduce-only",
    ]

    cli = importlib.import_module("gpt_trader.cli")
    services = importlib.import_module("gpt_trader.cli.services")

    class DummyConfig:
        def __init__(self, profile: str):
            self.profile = profile

    captured = {}

    def fake_build_config(args, *, include=None, skip=None):
        captured["skip"] = set(skip or [])
        return DummyConfig(args.profile)

    shutdown_called = {"value": False}

    class DummyBroker:
        def preview_order(self, **payload):
            captured["payload"] = payload
            return {"status": "ok"}

        def edit_order_preview(self, order_id, **payload):
            return {"order_id": order_id, "payload": payload}

        def edit_order(self, order_id, preview_id, **payload):
            return {"order_id": order_id, "preview_id": preview_id, "payload": payload}

    class DummyBot:
        def __init__(self):
            self.broker = DummyBroker()

        async def shutdown(self):
            shutdown_called["value"] = True

    monkeypatch.setattr(services, "build_config_from_args", fake_build_config)
    monkeypatch.setattr(services, "instantiate_bot", lambda config: DummyBot())

    printed = []

    def fake_print(value):
        printed.append(value)

    monkeypatch.setattr(builtins, "print", fake_print)

    exit_code = cli.main(argv)

    assert exit_code == 0
    assert shutdown_called["value"] is True
    payload = captured["payload"]
    assert payload["symbol"] == "BTC-PERP"
    assert payload["side"].name == "BUY"
    assert payload["order_type"].name == "LIMIT"
    assert str(payload["quantity"]) == "0.50"
    assert payload["reduce_only"] is True
    assert payload["leverage"] == 3
    assert any("status" in line for line in printed)


def test_cli_preflight_delegates_to_preflight_cli(monkeypatch):
    cli = importlib.import_module("gpt_trader.cli")
    preflight_cmd = importlib.import_module("gpt_trader.cli.commands.preflight")

    captured = {}

    def fake_run(argv=None):
        captured["argv"] = list(argv or [])
        return 0

    monkeypatch.setattr(preflight_cmd, "run_preflight_cli", fake_run)

    exit_code = cli.main(["preflight", "--profile", "prod", "--warn-only"])

    assert exit_code == 0
    assert captured["argv"] == ["--profile", "prod", "--warn-only"]


def test_orders_profile_argument_inherits_across_nested_subcommands():
    cli = importlib.import_module("gpt_trader.cli")
    parser = cli._build_parser()

    history_from_parent = parser.parse_args(["orders", "--profile", "prod", "history", "list"])
    assert history_from_parent.profile == "prod"

    history_from_leaf = parser.parse_args(["orders", "history", "list", "--profile", "prod"])
    assert history_from_leaf.profile == "prod"

    preview_from_parent = parser.parse_args(
        [
            "orders",
            "--profile",
            "prod",
            "preview",
            "--symbol",
            "BTC-PERP",
            "--side",
            "buy",
            "--type",
            "market",
            "--quantity",
            "1",
        ]
    )
    assert preview_from_parent.profile == "prod"
