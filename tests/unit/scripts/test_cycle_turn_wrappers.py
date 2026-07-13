"""Contract tests for the Stage-1 and Stage-2 scheduler wrappers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_wrapper(
    tmp_path: Path,
    script_name: str,
    *,
    proposers: str | None = None,
) -> list[str]:
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    call_log = tmp_path / "uv-calls.log"
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s|%s|%s\\n' "
        '"${GPT_TRADER_IDEAS_AUTO_APPROVAL:-}" '
        '"${GPT_TRADER_IDEAS_AUTO_EXECUTION:-}" "$*" >> "${UV_CALL_LOG}"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.update({"HOME": str(home), "UV_CALL_LOG": str(call_log)})
    env.pop("GPT_TRADER_IDEAS_AUTO_APPROVAL", None)
    env.pop("GPT_TRADER_IDEAS_AUTO_EXECUTION", None)
    if proposers is None:
        env.pop("CYCLE_PROPOSERS", None)
    else:
        env["CYCLE_PROPOSERS"] = proposers

    subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "ops" / script_name)],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )
    return call_log.read_text(encoding="utf-8").splitlines()


def test_stage2_wrapper_uses_accepted_benchmark_set_and_paper_gates(tmp_path: Path) -> None:
    calls = _run_wrapper(tmp_path, "stage2_cycle_turn.sh")

    assert calls[0] == "1|1|run gpt-trader ideas approve --auto-sweep --format json"
    assert calls[1].startswith("1|1|run gpt-trader ideas cycle --from-coinbase ")
    assert calls[1].count("--proposer") == 3
    assert "--proposer baseline" in calls[1]
    assert "--proposer regime-aware" in calls[1]
    assert "--proposer strategy-mean-reversion" in calls[1]
    assert "gpt-trader run" not in calls[1]


def test_stage2_wrapper_operator_override_replaces_benchmark_set(tmp_path: Path) -> None:
    calls = _run_wrapper(tmp_path, "stage2_cycle_turn.sh", proposers="baseline")

    assert calls[1].count("--proposer") == 1
    assert "--proposer baseline" in calls[1]
    assert "regime-aware" not in calls[1]
    assert "strategy-mean-reversion" not in calls[1]


def test_stage1_wrapper_keeps_cli_defaults_and_does_not_enable_stage2_gates(
    tmp_path: Path,
) -> None:
    (call,) = _run_wrapper(tmp_path, "stage1_cycle_turn.sh")

    assert call.startswith("||run gpt-trader ideas cycle --from-coinbase ")
    assert "--proposer" not in call


@pytest.mark.parametrize("script_name", ["stage1_cycle_turn.sh", "stage2_cycle_turn.sh"])
def test_wrapper_operator_override_remains_explicit(script_name: str, tmp_path: Path) -> None:
    calls = _run_wrapper(tmp_path, script_name, proposers="regime-aware baseline")
    cycle_call = calls[-1]

    assert cycle_call.count("--proposer") == 2
    assert "--proposer regime-aware --proposer baseline" in cycle_call
