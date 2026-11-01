"""RJ-1118: the trigger flags itself, and the scan can't be dodged."""

from __future__ import annotations

from pathlib import Path

import pytest

from gpt_trader.agents import trading_trigger as tt

HEAD = "abcdef1234567890"
REPO = Path(tt.__file__).resolve().parents[3]


def test_a_pr_that_adds_its_own_exception_is_flagged() -> None:
    # RJ-1118: excepting a live file and changing it in the same diff must not
    # read as a routine change.
    paths = [
        "src/gpt_trader/agents/trading_trigger.py",
        "src/gpt_trader/features/brokerages/coinbase/client/orders.py",
    ]
    result = tt.assess(paths, "Reserved: none", HEAD)
    assert result.severity == "blocker"
    assert "src/gpt_trader/agents/trading_trigger.py" in result.paths


def test_agent_pr_ready_runs_the_flagged_readiness_check() -> None:
    # The entry point must stay on the flagged files; pyproject.toml is not
    # flagged, so this pin (in a flagged file) is what holds it.
    import tomllib

    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["agent-pr-ready"] == "gpt_trader.agents.cli:pr_ready"
    cli = (REPO / "src/gpt_trader/agents/cli.py").read_text()
    assert '_run_script("pr_readiness.py")' in cli
    readiness = (REPO / "scripts/agents/pr_readiness.py").read_text()
    assert 'TRIGGER_PATH = "src/gpt_trader/agents/trading_trigger.py"' in readiness
    assert "trading_trigger.assess(" in readiness
    assert "base_trigger=base_trigger" in readiness


EVASIONS = {
    # Each built a real CoinbaseClient in Grok Scout's RJ-1118 check.
    "container": (
        "from gpt_trader.app.config import BotConfig\n"
        "from gpt_trader.app.container import ApplicationContainer as AC\n"
        "def broker_for(config: BotConfig):\n    return AC(config).broker\n"
    ),
    "importlib": (
        "import importlib\n"
        "m = importlib.import_module('gpt_trader.features.' + 'brokerages.factory')\n"
        "build = getattr(m, 'create_' + 'brokerage')\n"
    ),
    "aliased": (
        "from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient as C\n"
        "from gpt_trader.features.brokerages.coinbase.auth import SimpleAuth as A\n"
        "def client(key, pem):\n    return C(auth=A(key, pem))\n"
    ),
    "package_import": (
        "from gpt_trader.features import brokerages\n"
        "client = brokerages.coinbase.client.CoinbaseClient\n"
    ),
    "dunder_import": "m = __import__('gpt_trader.feat' + 'ures.brokerages.factory')\n",
}


@pytest.mark.parametrize("name", sorted(EVASIONS))
@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be", "utf-8-sig", "utf-32"]
)
def test_backstop_catches_broker_building_evasions(
    tmp_path: Path, name: str, encoding: str
) -> None:
    # A wildcard exception's files are scanned, so the path rule and the scan
    # together catch a new broker builder anywhere under src/gpt_trader.
    path = "src/gpt_trader/monitoring/runner.py"
    assert tt.trading_paths([path]) == []
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_bytes(EVASIONS[name].encode(encoding))
    assert tt.unflagged_live_capable(tmp_path, [path]) == [path]


def test_backstop_reads_a_python_coding_cookie(tmp_path: Path) -> None:
    # Python runs UTF-7 source, where "+AGM-" is "c", so the raw bytes hide the name.
    data = b"# -*- coding: utf-7 -*-\nbroker = +AGM-reate_brokerage(config)\n"
    assert "create_brokerage" in data.decode("utf-7")
    (tmp_path / "tool.py").write_bytes(data)
    assert tt.unflagged_live_capable(tmp_path, ["tool.py"]) == ["tool.py"]


def test_backstop_scans_exactly_named_exceptions(tmp_path: Path) -> None:
    # An excepted file that starts building a broker fails until reclassified.
    path = "src/gpt_trader/cli/response.py"
    assert path in tt.TRADING_EXCEPT
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text("client = CoinbaseClient(auth=auth)\n")
    assert tt.unflagged_live_capable(tmp_path, [path]) == [path]


def test_backstop_ignores_resource_loading(tmp_path: Path) -> None:
    (tmp_path / "tool.py").write_text("from importlib import resources\n")
    assert tt.unflagged_live_capable(tmp_path, ["tool.py"]) == []


@pytest.mark.parametrize(
    "cookie", ["idna", "undefined", "punycode", "rot13", "no-such-codec", "hex"]
)
def test_backstop_survives_any_coding_cookie(tmp_path: Path, cookie: str) -> None:
    # A cookie naming an odd codec must not crash the scan or hide other files.
    (tmp_path / "odd.py").write_bytes(f"# coding: {cookie}\nx = 1\n".encode() + b"\xff-+")
    (tmp_path / "tool.py").write_text("broker = create_brokerage(config)\n")
    assert tt.unflagged_live_capable(tmp_path, ["odd.py", "tool.py"]) == ["tool.py"]


def test_backstop_catches_alpaca_keys(tmp_path: Path) -> None:
    (tmp_path / "tool.py").write_text('key = os.environ["ALPACA_API_SECRET_KEY"]\n')
    assert tt.unflagged_live_capable(tmp_path, ["tool.py"]) == ["tool.py"]
