from __future__ import annotations

import pytest
from tests.unit.gpt_trader.features.data.data_module_test_helpers import _make_frame

from gpt_trader.features.data.types import DataQuery


def test_export_data_writes_csv(tmp_path, monkeypatch: pytest.MonkeyPatch, data_service) -> None:
    frame = _make_frame()

    monkeypatch.setattr(
        data_service["service"],
        "fetch_data",
        lambda query, use_cache=True: frame,
    )

    query = DataQuery(
        symbols=["BTC-USD"],
        start_date=frame.index.min(),
        end_date=frame.index.max(),
    )

    export_dir = tmp_path / "exports"
    success = data_service["service"].export_data(query, format="csv", path=str(export_dir))
    assert success

    exported_files = list(export_dir.glob("*.csv"))
    assert exported_files, "CSV export should create a file"


def test_import_data_roundtrips_csv(
    tmp_path, monkeypatch: pytest.MonkeyPatch, data_service
) -> None:
    frame = _make_frame()
    filepath = tmp_path / "input.csv"
    frame.to_csv(filepath)

    store_calls: list[dict[str, object]] = []

    def _store_stub(**kwargs):
        store_calls.append(kwargs)
        return True

    monkeypatch.setattr(data_service["service"], "store_data", _store_stub)

    assert data_service["service"].import_data(str(filepath), symbol="BTC-USD")
    assert store_calls, "store_data should be called for imported data"


def test_import_data_rejects_unknown_format(monkeypatch: pytest.MonkeyPatch, data_service) -> None:
    monkeypatch.setattr(data_service["service"], "store_data", lambda *_, **__: True)
    assert not data_service["service"].import_data("data.unsupported", symbol="BTC-USD")
