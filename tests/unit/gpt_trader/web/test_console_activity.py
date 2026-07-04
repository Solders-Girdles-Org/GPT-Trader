"""Activity page: cycle-turn feed from the manifest plus recent audit events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpt_trader.features.trade_ideas.service import TradeIdeaService
from gpt_trader.web import create_app
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

_NOW = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)

_COMPLETED_ROW = {
    "run_id": "cycle-20260704T090000Z-abc123",
    "started_at": "2026-07-04T09:00:00+00:00",
    "finished_at": "2026-07-04T09:00:42+00:00",
    "outcome": "completed",
    "error": None,
    "snapshot": {
        "source": "coinbase",
        "symbols": ["BTC-USD", "ETH-USD"],
    },
    "proposers": [
        {
            "proposer_id": "baseline-v1",
            "proposal_count": 2,
            "proposed_decision_ids": ["trade-a", "trade-b"],
            "skipped_open_instruments": [{"instrument": "SOL-USD", "reason": "open idea"}],
        }
    ],
    "execution": {"enabled": True, "executed": [{"decision_id": "trade-c"}], "skipped": []},
    "queue": {"pending_total": 3},
}

_FAILED_ROW = {
    "run_id": "cycle-20260704T100000Z-def456",
    "started_at": "2026-07-04T10:00:00+00:00",
    "finished_at": "2026-07-04T10:00:05+00:00",
    "outcome": "failed",
    "error": "ValidationError: snapshot fetch failed",
}


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(tmp_path, now_factory=lambda: _NOW)


@pytest.fixture
def client(service: TradeIdeaService, tmp_path: Path) -> TestClient:
    return TestClient(create_app(service=service, ideas_root=tmp_path, actor_id="rj"))


def _write_manifest(ideas_root: Path, *rows: object) -> None:
    cycle_root = ideas_root / "cycle"
    cycle_root.mkdir(parents=True, exist_ok=True)
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    (cycle_root / "manifest.jsonl").write_text(
        "".join(f"{line}\n" for line in lines), encoding="utf-8"
    )


def test_activity_renders_cycle_turns_newest_first(tmp_path: Path, client: TestClient) -> None:
    _write_manifest(tmp_path, _COMPLETED_ROW, _FAILED_ROW)

    response = client.get("/activity")

    assert response.status_code == 200
    assert "cycle-20260704T090000Z-abc123" in response.text
    assert "cycle-20260704T100000Z-def456" in response.text
    assert response.text.index("def456") < response.text.index("abc123")
    assert "ValidationError: snapshot fetch failed" in response.text
    assert "baseline-v1: 2 (1 skipped)" in response.text
    assert "BTC-USD, ETH-USD" in response.text
    assert "1 completed · 1 failed" in response.text


def test_activity_tolerates_unreadable_manifest_lines(tmp_path: Path, client: TestClient) -> None:
    _write_manifest(tmp_path, _COMPLETED_ROW, "{truncated")

    response = client.get("/activity")

    assert response.status_code == 200
    assert "cycle-20260704T090000Z-abc123" in response.text
    assert "unreadable manifest line" in response.text


def test_activity_treats_corrupt_row_fields_as_unreadable(
    tmp_path: Path, client: TestClient
) -> None:
    # Valid JSON with a corrupt field (schema drift, manual repair) must be
    # counted as unreadable, not turn the page into a 500.
    corrupt_row = {
        "run_id": "cycle-20260704T110000Z-bad999",
        "outcome": "completed",
        "proposers": [{"proposer_id": "baseline-v1", "proposal_count": "not-a-number"}],
    }
    _write_manifest(tmp_path, _COMPLETED_ROW, corrupt_row)

    response = client.get("/activity")

    assert response.status_code == 200
    assert "cycle-20260704T090000Z-abc123" in response.text
    assert "cycle-20260704T110000Z-bad999" not in response.text
    assert "unreadable manifest line" in response.text


def test_activity_renders_empty_state_without_manifest(client: TestClient) -> None:
    response = client.get("/activity")

    assert response.status_code == 200
    assert "No cycle turns recorded yet" in response.text


def test_activity_lists_recent_audit_events(service: TradeIdeaService, client: TestClient) -> None:
    attest_account_equity(service)
    service.propose(build_trade_idea(), actor_id="idea-generator-v1")
    service.approve("trade-20260612-001", actor_id="rj", reason="Risk verified")

    response = client.get("/activity")

    assert response.status_code == 200
    assert "Recent audit events" in response.text
    assert "idea-generator-v1" in response.text
    assert "Risk verified" in response.text
    assert '/ideas/trade-20260612-001"' in response.text
