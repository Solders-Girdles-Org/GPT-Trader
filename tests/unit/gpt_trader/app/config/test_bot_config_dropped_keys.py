"""BotConfig.from_dict names the YAML keys it drops instead of ignoring them silently (#1323)."""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.config import bot_config as bot_config_module
from gpt_trader.cli.services import load_config_from_yaml


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body)
    return path


def _dropped_key_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == bot_config_module.logger.name and record.levelno == logging.WARNING
    ]


@pytest.fixture(autouse=True)
def _capture_bot_config_warnings(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=bot_config_module.logger.name)


def test_known_keys_load_without_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _write(
        tmp_path,
        "good.yaml",
        "risk: {target_leverage: 2, stop_loss_pct: 0.01}\nsymbols: [SOL-USD]\n",
    )

    config = load_config_from_yaml(path)

    assert config.risk.target_leverage == 2
    assert config.risk.stop_loss_pct == Decimal("0.01")
    assert config.symbols == ["SOL-USD"]
    assert _dropped_key_warnings(caplog) == []


def test_misspelled_keys_are_named_in_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(
        tmp_path,
        "bad.yaml",
        "risk: {target_leverge: 2, stop_los_pct: 0.01}\nsymbol: [SOL-USD]\n",
    )

    load_config_from_yaml(path)

    [record] = _dropped_key_warnings(caplog)
    for key in ("risk.target_leverge", "risk.stop_los_pct", "symbol"):
        assert key in record.getMessage()
    assert record.ignored_keys == ["risk.target_leverge", "risk.stop_los_pct", "symbol"]
    assert record.non_mapping_sections == []


def test_non_mapping_sections_are_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path, "nondict.yaml", "risk: 0.01\nstrategy: fast\n")

    load_config_from_yaml(path)

    [record] = _dropped_key_warnings(caplog)
    assert "not mappings" in record.getMessage()
    assert record.non_mapping_sections == ["strategy (str)", "risk (float)"]
    assert record.ignored_keys == []


def test_empty_sections_load_defaults_without_warning(caplog: pytest.LogCaptureFixture) -> None:
    BotConfig.from_dict({"risk": None, "strategy": None, "symbols": ["BTC-USD"]})

    assert _dropped_key_warnings(caplog) == []


@pytest.mark.parametrize(
    ("body", "type_name"),
    [("- BTC-USD\n- ETH-USD\n", "list"), ("just a string\n", "str"), ("42\n", "int")],
)
def test_non_mapping_root_fails_with_path(tmp_path: Path, body: str, type_name: str) -> None:
    path = _write(tmp_path, "root.yaml", body)

    with pytest.raises(ValueError, match=rf"{re.escape(str(path))}.*mapping.*{type_name}"):
        load_config_from_yaml(path)


def test_empty_file_loads_defaults(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _write(tmp_path, "empty.yaml", "")

    config = load_config_from_yaml(path)

    assert config.risk.target_leverage == BotConfig().risk.target_leverage
    assert _dropped_key_warnings(caplog) == []
