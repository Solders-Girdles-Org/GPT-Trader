"""BotConfig.from_dict names the YAML keys it drops instead of ignoring them silently (#1323).

The library path warns; the ``run --config`` CLI path rejects them (#1329).
"""

from __future__ import annotations

import logging
import re
from argparse import Namespace
from decimal import Decimal
from pathlib import Path

import pytest

import gpt_trader.cli.commands.run as run_cmd
from gpt_trader.app.config import BotConfig
from gpt_trader.app.config import bot_config as bot_config_module
from gpt_trader.app.config.validation import ConfigValidationError
from gpt_trader.cli.services import load_config_from_yaml

BAD_YAML = "risk: {target_leverge: 2, stop_los_pct: 0.01}\nsymbol: [SOL-USD]\n"


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
    path = _write(tmp_path, "bad.yaml", BAD_YAML)

    BotConfig.from_yaml(path)

    [record] = _dropped_key_warnings(caplog)
    for key in ("risk.target_leverge", "risk.stop_los_pct", "symbol"):
        assert key in record.getMessage()
    assert record.ignored_keys == ["risk.target_leverge", "risk.stop_los_pct", "symbol"]
    assert record.non_mapping_sections == []


def test_non_mapping_sections_are_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path, "nondict.yaml", "risk: 0.01\nstrategy: fast\n")

    BotConfig.from_yaml(path)

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


def test_cli_loader_rejects_dropped_keys_naming_file_and_keys(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.yaml", BAD_YAML)

    with pytest.raises(ConfigValidationError) as exc_info:
        load_config_from_yaml(path)

    [error] = exc_info.value.errors
    assert str(path) in error
    for key in ("risk.target_leverge", "risk.stop_los_pct", "symbol"):
        assert key in error


def test_cli_loader_rejects_non_mapping_sections(tmp_path: Path) -> None:
    path = _write(tmp_path, "nondict.yaml", "risk: 0.01\nstrategy: fast\n")

    with pytest.raises(ConfigValidationError, match=r"strategy \(str\), risk \(float\)") as exc:
        load_config_from_yaml(path)

    assert str(path) in str(exc.value)


def test_strict_from_dict_raises_instead_of_warning(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(ConfigValidationError, match=r"unknown keys: risk\.target_leverge"):
        BotConfig.from_dict({"risk": {"target_leverge": 2}}, strict=True)

    assert _dropped_key_warnings(caplog) == []


def test_strict_from_dict_accepts_known_keys() -> None:
    config = BotConfig.from_dict({"risk": {"target_leverage": 3}, "strategy": None}, strict=True)

    assert config.risk.target_leverage == 3


def test_run_config_fails_before_bot_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path, "bad.yaml", BAD_YAML)

    def _must_not_build(config: BotConfig) -> None:
        raise AssertionError("bot built from a config with dropped keys")

    monkeypatch.setattr(run_cmd.services, "instantiate_bot", _must_not_build)
    caplog.set_level(logging.ERROR, logger=run_cmd.logger.name)

    result = run_cmd.execute(Namespace(config=str(path), dev_fast=False, profile=None))

    assert result == 1
    message = caplog.records[-1].getMessage()
    assert str(path) in message
    for key in ("risk.target_leverge", "risk.stop_los_pct", "symbol"):
        assert key in message
