"""Base installs must not load the optional Robinhood transport dependencies."""

from __future__ import annotations

import subprocess
import sys

import pytest

_BLOCK_OPTIONAL_IMPORTS = """
import importlib.abc
import sys

class NoAdapterDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'anyio', 'mcp', 'keyring', 'jsonschema', 'httpx'}:
            raise ModuleNotFoundError(f'Blocked optional dependency: {fullname}')

sys.meta_path.insert(0, NoAdapterDependencies())
"""


@pytest.mark.parametrize(
    "script",
    [
        "import gpt_trader",
        "import runpy; sys.argv = ['gpt-trader', '--help']; "
        "runpy.run_module('gpt_trader.cli', run_name='__main__')",
        """
import asyncio
from gpt_trader.features.brokerages.robinhood.agentic.transport import (
    McpRobinhoodAgenticGateway,
)
from gpt_trader.features.brokerages.robinhood.agentic.errors import (
    RobinhoodAgenticUnavailable,
)

try:
    asyncio.run(McpRobinhoodAgenticGateway.connect())
except RobinhoodAgenticUnavailable as exc:
    assert 'Install the robinhood-agentic extra' in str(exc), str(exc)
else:
    raise AssertionError('Optional adapter connected without its extra')
""",
    ],
    ids=["package-import", "cli-help", "missing-extra-error"],
)
def test_base_install_without_adapter_dependencies(script: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK_OPTIONAL_IMPORTS + script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
