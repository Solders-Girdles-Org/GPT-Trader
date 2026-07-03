"""
Minimal Conftest.
"""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def strict_container_mode(monkeypatch):
    """Enable strict container mode for all tests.

    This ensures tests fail fast if they use get_failure_tracker()
    without properly setting up an application container.
    """
    monkeypatch.setenv("GPT_TRADER_STRICT_CONTAINER", "1")


@pytest.fixture(autouse=True)
def isolated_ideas_root(tmp_path_factory, monkeypatch):
    """Pin the trade-ideas root away from any real on-disk state.

    With risk_budget_runtime_seed_enabled on by default (#1120), every
    ApplicationContainer startup resolves the active RiskBudget from the
    ideas root. Without this pin, tests that build a container would read
    whatever risk_budget.jsonl exists under the developer's CWD. Tests that
    care about specific budget contents set GPT_TRADER_IDEAS_ROOT (or pass
    an explicit root) themselves, which overrides this fixture.
    """
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(tmp_path_factory.mktemp("isolated-ideas-root")))


@pytest.fixture
def application_container(mock_config):
    """Provide an ApplicationContainer for tests that need it.

    This fixture sets up a container with the mock_config and registers
    it globally. It automatically clears the container after the test.

    Requires a mock_config fixture to be defined in the test module.
    """
    from gpt_trader.app.container import (
        ApplicationContainer,
        clear_application_container,
        set_application_container,
    )

    container = ApplicationContainer(mock_config)
    set_application_container(container)
    yield container
    clear_application_container()


@pytest.fixture(autouse=True)
def reset_correlation_context():
    """Reset correlation context before and after each test to prevent pollution."""
    from gpt_trader.logging.correlation import set_correlation_id, set_domain_context

    # Reset before test
    set_correlation_id("")
    set_domain_context({})

    yield

    # Reset after test
    set_correlation_id("")
    set_domain_context({})


def pytest_sessionfinish(session, exitstatus):
    """Clean up cache files after test session completes."""
    repo_root = Path(__file__).resolve().parents[1]
    cleanup_script = repo_root / "scripts" / "maintenance" / "cleanup_workspace.py"

    if cleanup_script.exists():
        subprocess.run(
            ["python", str(cleanup_script), "--apply", "--quiet", "--preserve-hypothesis"],
            cwd=repo_root,
            capture_output=True,
        )
