"""Unit-test files can be runtime configs despite the scan skip."""

from pathlib import Path

import pytest

from gpt_trader.agents import trading_trigger as tt
from gpt_trader.cli.services import _keys_set_false


@pytest.mark.parametrize(
    "path",
    [
        "tests/unit/run.yaml",
        "tests/unit/fixtures/live.yml",
        "tests/unit/gpt_trader/cfg.yaml",  # naming: allow - exact reported reproduction path
        "tests/unit/fixtures/live.json",
        "tests/unit/fixtures/live.txt",
        "tests/unit/fixtures/live.md",
        "tests/unit/fixtures/live",
        "Tests/Unit/live.yaml",
        "tests/Unit/live.yaml",
        "tests/unit/live.PY",
        "tests/unit/live.py",
    ],
)
def test_live_unit_config_is_caught(tmp_path: Path, path: str) -> None:
    config = tmp_path / path
    config.parent.mkdir(parents=True)
    config.write_text("symbols: [BTC-USD]\ndry_run: false\nmock_broker: false\n")

    # Prove the runtime loader would turn both safety switches off, without
    # building a container, reading keys or calling a broker.
    assert _keys_set_false(config) == {"dry_run", "mock_broker"}
    assert tt.unflagged_live_capable(tmp_path, [path]) == [path]


@pytest.mark.parametrize("text", ["dry_run: false\n", "mock_broker: false\n"])
def test_either_switch_off_in_unit_config_is_caught(tmp_path: Path, text: str) -> None:
    path = "tests/unit/config.yaml"
    config = tmp_path / path
    config.parent.mkdir(parents=True)
    config.write_text(text)
    assert tt.unflagged_live_capable(tmp_path, [path]) == [path]


@pytest.mark.parametrize("path", ["tests/unit/config.yaml", "Tests/Unit/config.py"])
def test_unit_config_with_switches_on_and_other_tokens_passes(tmp_path: Path, path: str) -> None:
    config = tmp_path / path
    config.parent.mkdir(parents=True)
    config.write_text("# COINBASE_API_KEY CoinbaseClient(\ndry_run: true\nmock_broker: true\n")
    assert _keys_set_false(config) == set()
    assert tt.unflagged_live_capable(tmp_path, [path]) == []


def test_python_unit_tests_keep_the_mock_only_exception(tmp_path: Path) -> None:
    path = "tests/unit/test_example.py"
    test = tmp_path / path
    test.parent.mkdir(parents=True)
    test.write_text('config = "dry_run: false\\nmock_broker: false\\n"\n# CoinbaseClient(\n')
    assert tt.trading_paths([path]) == []
    assert tt.unflagged_live_capable(tmp_path, [path]) == []


@pytest.mark.parametrize(
    "text",
    [
        "dry_run: False\nmock_broker: False\n",
        "{dry_run: false, mock_broker: false}\n",
        "---\ntrading:\n  execution:\n    dry_run: false\n    mock_broker: false\n",
    ],
)
def test_python_extension_yaml_mapping_is_caught(tmp_path: Path, text: str) -> None:
    path = "Tests/Unit/live.py"
    config = tmp_path / path
    config.parent.mkdir(parents=True)
    config.write_text(text)
    assert _keys_set_false(config) == {"dry_run", "mock_broker"}
    assert tt.unflagged_live_capable(tmp_path, [path]) == [path]


@pytest.mark.parametrize(
    "text", ['["dry_run: false"]\n', '"dry_run: false"\n', "dry_run: [false\n"]
)
def test_python_extension_without_a_yaml_mapping_stays_exempt(tmp_path: Path, text: str) -> None:
    path = "tests/unit/helper.py"
    config = tmp_path / path
    config.parent.mkdir(parents=True)
    config.write_text(text)
    assert tt.unflagged_live_capable(tmp_path, [path]) == []
