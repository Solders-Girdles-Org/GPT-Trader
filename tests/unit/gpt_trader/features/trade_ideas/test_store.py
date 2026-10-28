from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.trade_ideas import MaxLoss, TradeIdeaStore


@pytest.fixture
def store(tmp_path: Path) -> TradeIdeaStore:
    return TradeIdeaStore(tmp_path / "records")


def test_save_and_load_latest(store: TradeIdeaStore) -> None:
    idea = build_trade_idea()
    store.save(idea)

    assert store.load_latest(idea.decision_id) == idea


def test_missing_decision_returns_none(store: TradeIdeaStore) -> None:
    assert store.load_latest("trade-unknown") is None
    assert store.load_version("trade-unknown", "deadbeef") is None


def test_every_version_stays_retrievable_by_hash(store: TradeIdeaStore) -> None:
    original = build_trade_idea()
    first_hash = store.save(original)
    amended = build_trade_idea(max_loss=MaxLoss(amount=Decimal("300")))
    second_hash = store.save(amended)

    assert store.load_version(original.decision_id, first_hash) == original
    assert store.load_version(original.decision_id, second_hash) == amended
    assert store.load_latest(original.decision_id) == amended


def test_list_decision_ids_sorted(store: TradeIdeaStore) -> None:
    store.save(build_trade_idea(decision_id="trade-20260612-002"))
    store.save(build_trade_idea(decision_id="trade-20260612-001"))

    assert store.list_decision_ids() == ["trade-20260612-001", "trade-20260612-002"]


def test_empty_store_lists_nothing(store: TradeIdeaStore) -> None:
    assert store.list_decision_ids() == []


@pytest.mark.parametrize("unsafe", ["../outside", "/tmp/outside", "a/b", "a\\b", ".", "..", ""])
def test_rejects_unsafe_lookup_paths(store: TradeIdeaStore, unsafe: str) -> None:
    with pytest.raises(ValueError, match="decision_id"):
        store.exists(unsafe)
    with pytest.raises(ValueError, match="decision_id"):
        store.load_latest(unsafe)
    with pytest.raises(ValueError, match="decision_id"):
        store.load_version(unsafe, "deadbeef")
    with pytest.raises(ValueError, match="record_hash"):
        store.load_version("trade-safe", unsafe)
