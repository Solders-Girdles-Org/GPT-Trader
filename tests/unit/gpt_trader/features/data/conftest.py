"""Fixtures shared by the DataService tests in this package."""

from __future__ import annotations

import pytest
from tests.unit.gpt_trader.features.data.data_module_test_helpers import (
    CacheStub,
    QualityStub,
    StorageStub,
)

from gpt_trader.features.data.data import DataService


@pytest.fixture
def data_service():
    storage = StorageStub()
    cache = CacheStub()
    quality = QualityStub()
    service = DataService(storage=storage, cache=cache, quality_checker=quality)
    return {"service": service, "storage": storage, "cache": cache, "quality": quality}
