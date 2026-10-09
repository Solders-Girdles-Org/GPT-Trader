"""Tests for the repository pattern scanner."""

from __future__ import annotations

from pathlib import Path

import pytest
import scripts.ci.check_legacy_patterns as check_legacy_patterns


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestDeprecatedEnvUsage:
    """Coverage for deprecated env var detection."""

    def test_env_var_allowed_in_allowlist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(check_legacy_patterns, "REPO_ROOT", tmp_path)
        allowed_path = tmp_path / "docs" / "DEPRECATIONS.md"
        # Explicit `+` keeps the token split (so the guard does not flag this file)
        # and survives `ruff format`, which joins implicit concatenations.
        env_var = "COINBASE_ENABLE_" + "DERIVATIVES"
        _write_file(allowed_path, f"{env_var}=1\n")

        errors = check_legacy_patterns._check_deprecated_env_usage([allowed_path])

        assert errors == []

    def test_env_var_disallowed_elsewhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(check_legacy_patterns, "REPO_ROOT", tmp_path)
        disallowed_path = tmp_path / "notes.md"
        env_var = "COINBASE_ENABLE_" + "DERIVATIVES"
        _write_file(disallowed_path, f"{env_var}=1\n")

        errors = check_legacy_patterns._check_deprecated_env_usage([disallowed_path])

        env_var = "COINBASE_ENABLE_" + "DERIVATIVES"
        assert errors == [f"notes.md: legacy env var '{env_var}' referenced outside allowlist"]


class TestBlockingCallsInAsync:
    """Coverage for async blocking call detection."""

    def test_blocking_calls_inside_async_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(check_legacy_patterns, "REPO_ROOT", tmp_path)
        file_path = tmp_path / "src" / "example.py"
        _write_file(
            file_path,
            "import time\n"
            "import requests\n"
            "\n"
            "async def do_work():\n"
            "    time."
            "sleep(1)\n"
            "    requests.get('https://example.com')\n",
        )

        errors = check_legacy_patterns._check_blocking_calls_in_async([file_path])

        assert any("time.sleep inside async def" in error for error in errors)
        assert any("requests.get inside async def" in error for error in errors)

    def test_blocking_calls_inside_sync_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(check_legacy_patterns, "REPO_ROOT", tmp_path)
        file_path = tmp_path / "src" / "example.py"
        _write_file(
            file_path,
            "import time\n" "\n" "def do_work():\n" "    time." + "sleep(1)\n",
        )

        errors = check_legacy_patterns._check_blocking_calls_in_async([file_path])

        assert errors == []


class TestDuplicateDeployEntrypoints:
    """Coverage for legacy deploy layout detection."""

    def test_duplicate_entrypoints_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(check_legacy_patterns, "REPO_ROOT", tmp_path)
        allowed_compose = tmp_path / "deploy" / "gpt_trader" / "docker" / "docker-compose.yaml"
        disallowed_compose = tmp_path / "deploy" / "docker-compose.yaml"
        legacy_kubernetes = tmp_path / "deploy" / "legacy" / "kubernetes" / "job.yaml"

        for path in (allowed_compose, disallowed_compose, legacy_kubernetes):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

        errors = check_legacy_patterns._check_duplicate_deploy_entrypoints(
            [allowed_compose, disallowed_compose, legacy_kubernetes]
        )

        assert "deploy/docker-compose.yaml: unexpected docker-compose file" in errors
        assert (
            "deploy/legacy/kubernetes/job.yaml: legacy kubernetes deployment directory detected"
            in errors
        )
        assert all("deploy/gpt_trader/docker/docker-compose.yaml" not in error for error in errors)
