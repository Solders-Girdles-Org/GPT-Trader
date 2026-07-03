"""Tests for the immutable runtime settings snapshot helpers."""

from __future__ import annotations

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.runtime.settings import (
    FrozenConfigProxy,
    RuntimeSettingsSnapshot,
    create_runtime_settings_snapshot,
    ensure_runtime_settings_snapshot,
)


def test_runtime_settings_snapshot_is_immutable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot data should be immutable and include captured env vars."""
    monkeypatch.setenv("GPT_TRADER_TEST_OVERRIDE", "snapshot-value")

    config = BotConfig()
    config.symbols = ["BTC-USD"]
    snapshot = create_runtime_settings_snapshot(config)

    assert snapshot.env_vars["GPT_TRADER_TEST_OVERRIDE"] == "snapshot-value"
    assert isinstance(snapshot.config_data["symbols"], tuple)
    assert snapshot.config_data.risk.target_leverage == config.risk.target_leverage
    serialized = snapshot.serialize()
    assert serialized == snapshot.serialize()

    with pytest.raises(AttributeError):
        snapshot.config_data.symbols = ["ETH-USD"]

    with pytest.raises(AttributeError):
        snapshot.config_data.risk = None

    with pytest.raises(AttributeError):
        snapshot.config_data["symbols"].append("ETH-USD")  # tuple doesn't support append


def test_ensure_runtime_settings_snapshot_handles_both_types() -> None:
    """Ensure helper returns snapshots for BotConfig and passes through existing snapshots."""
    config = BotConfig()
    snapshot = create_runtime_settings_snapshot(config)

    assert ensure_runtime_settings_snapshot(snapshot) is snapshot
    result = ensure_runtime_settings_snapshot(config)

    assert isinstance(result, RuntimeSettingsSnapshot)
    assert result is not snapshot


def test_runtime_settings_snapshot_handles_opaque_metadata_values() -> None:
    """Snapshot generation should not deepcopy arbitrary metadata objects."""

    class _OpaqueObject:
        def __deepcopy__(self, memo):  # pragma: no cover - guardrail for behavior
            raise TypeError("opaque object cannot be deep-copied")

    config = BotConfig()
    opaque = _OpaqueObject()
    config.metadata["opaque"] = opaque

    snapshot = create_runtime_settings_snapshot(config)

    assert snapshot.config_data.metadata.opaque is opaque


def test_runtime_settings_snapshot_nested_structures_are_immutable() -> None:
    """Nested dict/list/tuple views should remain read-only."""
    config = BotConfig()
    config.metadata["nested"] = {
        "sequence": ["BTC-USD", "ETH-USD"],
        "flags": {"enabled": True, "threshold": 5},
    }

    snapshot = create_runtime_settings_snapshot(config)

    nested = snapshot.config_data.metadata.nested
    assert isinstance(nested, FrozenConfigProxy)
    assert isinstance(nested.sequence, tuple)
    assert isinstance(nested.flags, FrozenConfigProxy)

    with pytest.raises(AttributeError):
        nested.sequence += ("XRP-USD",)

    with pytest.raises(AttributeError):
        nested.flags.enabled = False

    with pytest.raises(TypeError):
        nested.flags["enabled"] = False


def test_runtime_settings_snapshot_detaches_from_mutable_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changes after capture should not affect snapshot contents."""
    monkeypatch.setenv("RUNTIME_SNAPSHOT_TEST", "initial")
    config = BotConfig()
    config.symbols = ["BTC-USD"]
    config.metadata["nested"] = {"values": [1, 2]}

    snapshot = create_runtime_settings_snapshot(config)

    config.symbols.append("ETH-USD")
    config.metadata["nested"]["values"].append(3)
    monkeypatch.setenv("RUNTIME_SNAPSHOT_TEST", "updated")
    monkeypatch.setenv("ADDITIONAL_VAR", "value")

    assert snapshot.config_data.symbols == ("BTC-USD",)
    assert snapshot.config_data.metadata.nested["values"] == (1, 2)
    assert snapshot.env_vars["RUNTIME_SNAPSHOT_TEST"] == "initial"
    assert "ADDITIONAL_VAR" not in snapshot.env_vars


def test_runtime_settings_snapshot_serialization_is_functionally_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serialization output stays stable regardless of insertion order."""
    config = BotConfig()
    monkeypatch.setenv("ORDER_TEST_ONE", "first")
    monkeypatch.setenv("ORDER_TEST_TWO", "second")

    first_snapshot = create_runtime_settings_snapshot(config)

    monkeypatch.delenv("ORDER_TEST_ONE", raising=False)
    monkeypatch.delenv("ORDER_TEST_TWO", raising=False)
    monkeypatch.setenv("ORDER_TEST_TWO", "second")
    monkeypatch.setenv("ORDER_TEST_ONE", "first")

    second_snapshot = create_runtime_settings_snapshot(config)

    assert first_snapshot.serialize() == second_snapshot.serialize()
