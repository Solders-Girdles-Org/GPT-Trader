"""Tests for system preflight checks (Python version, disk space, time)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

import gpt_trader.preflight.checks.system as system_checks
from gpt_trader.preflight.checks.system import (
    check_disk_space,
    check_python_version,
    check_system_time,
)
from gpt_trader.preflight.core import PreflightCheck

FAKE_COINBASE_PRIVATE_KEY = "test-private-key"


class TestCheckPythonVersion:
    """Test Python version check."""

    def test_passes_on_python_312_or_higher(self, capsys: pytest.CaptureFixture) -> None:
        """Should pass on Python 3.12+."""
        checker = PreflightCheck()

        # sys.version_info is actually running, and we're on 3.12+
        # Just verify the check passes with the real Python version
        result = check_python_version(checker)

        # We're running on Python 3.12+, so this should pass
        assert result is True
        assert any("meets requirements" in s for s in checker.successes)

    def test_passes_on_current_python(self, capsys: pytest.CaptureFixture) -> None:
        """Should pass on current Python version (3.12+)."""
        checker = PreflightCheck()
        result = check_python_version(checker)

        # Tests only run on Python 3.12+, so this should always pass
        assert result is True

    def test_logs_python_version(self, capsys: pytest.CaptureFixture) -> None:
        """Should log the Python version."""
        checker = PreflightCheck()
        check_python_version(checker)

        captured = capsys.readouterr()
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in captured.out

    def test_version_check_logic(self) -> None:
        """Test the version check logic directly."""
        # The check passes if major == 3 and minor >= 12
        assert sys.version_info.major == 3
        assert sys.version_info.minor >= 12  # Project requires 3.12+

    def test_prints_section_header(self, capsys: pytest.CaptureFixture) -> None:
        """Should print section header."""
        checker = PreflightCheck()
        check_python_version(checker)

        captured = capsys.readouterr()
        assert "PYTHON VERSION CHECK" in captured.out


class TestCheckDiskSpace:
    """Test disk space check."""

    @pytest.fixture
    def disk_usage_stub(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        stub = MagicMock()
        monkeypatch.setattr(system_checks.shutil, "disk_usage", stub)
        return stub

    def test_passes_with_plenty_of_space(
        self, capsys: pytest.CaptureFixture, disk_usage_stub: MagicMock
    ) -> None:
        """Should pass with >1GB free."""
        checker = PreflightCheck()

        # Mock 100GB total, 50GB free
        mock_usage = MagicMock(
            total=100 * 1024**3,
            free=50 * 1024**3,
            used=50 * 1024**3,
        )

        disk_usage_stub.return_value = mock_usage
        result = check_disk_space(checker)

        assert result is True
        assert any("Disk space" in s for s in checker.successes)

    def test_warns_with_low_space(
        self, capsys: pytest.CaptureFixture, disk_usage_stub: MagicMock
    ) -> None:
        """Should warn with 0.5-1GB free."""
        checker = PreflightCheck()

        # Mock 100GB total, 0.7GB free
        mock_usage = MagicMock(
            total=100 * 1024**3,
            free=int(0.7 * 1024**3),
            used=int(99.3 * 1024**3),
        )

        disk_usage_stub.return_value = mock_usage
        result = check_disk_space(checker)

        assert result is True  # Still passes but with warning
        assert any("Low disk space" in w for w in checker.warnings)

    def test_fails_with_critical_space(
        self, capsys: pytest.CaptureFixture, disk_usage_stub: MagicMock
    ) -> None:
        """Should fail with <0.5GB free."""
        checker = PreflightCheck()

        # Mock 100GB total, 0.3GB free
        mock_usage = MagicMock(
            total=100 * 1024**3,
            free=int(0.3 * 1024**3),
            used=int(99.7 * 1024**3),
        )

        disk_usage_stub.return_value = mock_usage
        result = check_disk_space(checker)

        assert result is False
        assert any("Critical" in e for e in checker.errors)

    def test_handles_exception(
        self, capsys: pytest.CaptureFixture, disk_usage_stub: MagicMock
    ) -> None:
        """Should handle exceptions gracefully."""
        checker = PreflightCheck()

        disk_usage_stub.side_effect = OSError("Permission denied")
        result = check_disk_space(checker)

        assert result is False
        assert any("Failed to check disk space" in e for e in checker.errors)


class TestCheckSystemTime:
    """Test system time synchronization check."""

    @pytest.fixture
    def mock_datetime(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        stub = MagicMock()
        stub.fromisoformat = datetime.fromisoformat
        monkeypatch.setattr(system_checks, "datetime", stub)
        return stub

    @pytest.fixture
    def cleared_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in (
            "COINBASE_PREFLIGHT_FORCE_REMOTE",
            "COINBASE_PREFLIGHT_SKIP_REMOTE",
            "COINBASE_CREDENTIALS_FILE",
            "COINBASE_PROD_CDP_API_KEY",
            "COINBASE_CDP_API_KEY",
            "COINBASE_API_KEY_NAME",
            "COINBASE_PROD_CDP_PRIVATE_KEY",
            "COINBASE_CDP_PRIVATE_KEY",
            "COINBASE_PRIVATE_KEY",
        ):
            monkeypatch.delenv(key, raising=False)

    def test_passes_with_reasonable_time_no_credentials(
        self,
        capsys: pytest.CaptureFixture,
        mock_datetime: MagicMock,
        cleared_env: None,
    ) -> None:
        """Should pass with reasonable time when no credentials available."""
        checker = PreflightCheck(profile="dev")

        mock_now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        mock_datetime.now.return_value = mock_now
        result = check_system_time(checker)

        assert result is True
        assert any("seems reasonable" in w for w in checker.warnings)

    def test_passes_with_credentialed_server_time(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_datetime: MagicMock,
        cleared_env: None,
    ) -> None:
        checker = PreflightCheck(profile="dev")
        monkeypatch.setenv("COINBASE_PREFLIGHT_FORCE_REMOTE", "1")
        monkeypatch.setenv("COINBASE_CDP_API_KEY", "organizations/abc/apiKeys/xyz")
        monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", FAKE_COINBASE_PRIVATE_KEY)
        mock_now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        mock_datetime.now.return_value = mock_now
        client = MagicMock()
        client.get_time.return_value = {"iso": "2025-06-15T12:00:00Z"}
        monkeypatch.setattr(system_checks, "_build_coinbase_time_client", lambda **_: client)

        result = check_system_time(checker)

        assert result is True
        assert any("synchronized" in s for s in checker.successes)

    def test_fails_closed_when_credentialed_time_client_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_datetime: MagicMock,
        cleared_env: None,
    ) -> None:
        checker = PreflightCheck(profile="dev")
        monkeypatch.setenv("COINBASE_PREFLIGHT_FORCE_REMOTE", "1")
        monkeypatch.setenv("COINBASE_CDP_API_KEY", "organizations/abc/apiKeys/xyz")
        monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", FAKE_COINBASE_PRIVATE_KEY)
        mock_datetime.now.return_value = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

        def raise_client_error(**_: str) -> object:
            raise RuntimeError("network unavailable")

        monkeypatch.setattr(system_checks, "_build_coinbase_time_client", raise_client_error)

        result = check_system_time(checker)

        assert result is False
        assert any("Cannot verify Coinbase server time" in e for e in checker.errors)
        assert not any("seems reasonable" in w for w in checker.warnings)

    def test_fails_closed_when_credentialed_time_response_missing_iso(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_datetime: MagicMock,
        cleared_env: None,
    ) -> None:
        checker = PreflightCheck(profile="dev")
        monkeypatch.setenv("COINBASE_PREFLIGHT_FORCE_REMOTE", "1")
        monkeypatch.setenv("COINBASE_CDP_API_KEY", "organizations/abc/apiKeys/xyz")
        monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", FAKE_COINBASE_PRIVATE_KEY)
        mock_datetime.now.return_value = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        client = MagicMock()
        client.get_time.return_value = {"epoch": 1_750_000_000}
        monkeypatch.setattr(system_checks, "_build_coinbase_time_client", lambda **_: client)

        result = check_system_time(checker)

        assert result is False
        assert any("missing 'iso'" in e for e in checker.errors)

    def test_explicit_remote_skip_preserves_local_time_sanity_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_datetime: MagicMock,
        cleared_env: None,
    ) -> None:
        checker = PreflightCheck(profile="dev")
        monkeypatch.setenv("COINBASE_PREFLIGHT_SKIP_REMOTE", "1")
        monkeypatch.setenv("COINBASE_CDP_API_KEY", "organizations/abc/apiKeys/xyz")
        monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", FAKE_COINBASE_PRIVATE_KEY)
        mock_datetime.now.return_value = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        client_factory = MagicMock()
        monkeypatch.setattr(system_checks, "_build_coinbase_time_client", client_factory)

        result = check_system_time(checker)

        assert result is True
        client_factory.assert_not_called()
        assert any("Skipping Coinbase server time verification" in w for w in checker.warnings)
        assert any("seems reasonable" in w for w in checker.warnings)

    def test_fails_with_unreasonable_time(
        self,
        capsys: pytest.CaptureFixture,
        mock_datetime: MagicMock,
        cleared_env: None,
    ) -> None:
        """Should fail with clearly wrong system time."""
        checker = PreflightCheck(profile="dev")

        # Mock datetime to return an unreasonable time (year 2015)
        mock_now = datetime(2015, 6, 15, 12, 0, 0, tzinfo=UTC)
        mock_datetime.now.return_value = mock_now
        result = check_system_time(checker)

        assert result is False
        assert any("seems wrong" in e for e in checker.errors)

    def test_handles_exception(
        self, capsys: pytest.CaptureFixture, mock_datetime: MagicMock
    ) -> None:
        """Should handle exceptions gracefully."""
        checker = PreflightCheck()

        mock_datetime.now.side_effect = Exception("Time error")
        result = check_system_time(checker)

        assert result is False
        assert any("Failed to check system time" in e for e in checker.errors)

    def test_prints_section_header(
        self,
        capsys: pytest.CaptureFixture,
        mock_datetime: MagicMock,
        cleared_env: None,
    ) -> None:
        """Should print section header."""
        checker = PreflightCheck(profile="dev")

        mock_now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        mock_datetime.now.return_value = mock_now
        check_system_time(checker)

        captured = capsys.readouterr()
        assert "SYSTEM TIME SYNC" in captured.out
