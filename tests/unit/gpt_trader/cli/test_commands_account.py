from __future__ import annotations

import argparse
import json
from argparse import Namespace

import pytest

import gpt_trader.cli.commands.account as account_cmd


def test_account_snapshot_inherits_parent_profile() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    account_cmd.register(subparsers)

    parent_profile_args = parser.parse_args(["account", "--profile", "prod", "snapshot"])
    assert parent_profile_args.profile == "prod"

    snapshot_profile_args = parser.parse_args(["account", "snapshot", "--profile", "prod"])
    assert snapshot_profile_args.profile == "prod"


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
