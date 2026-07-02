from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .checks import (
    check_api_connectivity,
    check_dependencies,
    check_disk_space,
    check_environment_variables,
    check_event_store_redaction,
    check_key_permissions,
    check_pretrade_diagnostics,
    check_profile_configuration,
    check_python_version,
    check_readiness_report,
    check_risk_configuration,
    check_system_time,
    check_test_suite,
    check_trade_ideas_readiness,
    simulate_dry_run,
)
from .context import PreflightContext
from .report import ReportTarget, generate_report
from .validation_result import PreflightResultPayload


@dataclass
class PreflightCheck:
    """Facade over individual preflight checks with shared logging/context."""

    verbose: bool = False
    profile: str = "canary"
    context: PreflightContext = field(init=False)

    def __post_init__(self) -> None:
        self.context = PreflightContext(verbose=self.verbose, profile=self.profile)

    # ----- Compatibility mirrors -------------------------------------------
    @property
    def errors(self) -> list[str]:
        return self.context.errors

    @property
    def warnings(self) -> list[str]:
        return self.context.warnings

    @property
    def successes(self) -> list[str]:
        return self.context.successes

    @property
    def results(self) -> list[PreflightResultPayload]:
        return self.context.results

    @property
    def config(self) -> dict:
        return self.context.config

    # Logging helpers (preserve original surface area)
    def log_success(self, message: str, details: Mapping[str, Any] | str | None = None) -> None:
        self.context.log_success(message, details=details)

    def log_warning(self, message: str, details: Mapping[str, Any] | str | None = None) -> None:
        self.context.log_warning(message, details=details)

    def log_error(self, message: str, details: Mapping[str, Any] | str | None = None) -> None:
        self.context.log_error(message, details=details)

    def log_info(self, message: str) -> None:
        self.context.log_info(message)

    def section_header(self, title: str) -> None:
        self.context.section_header(title)

    # Environment helpers
    def _resolve_cdp_credentials(self) -> tuple[str | None, str | None]:
        return self.context.resolve_cdp_credentials()

    def _has_real_cdp_credentials(self) -> bool:
        return self.context.has_real_cdp_credentials()

    def _should_skip_remote_checks(self) -> bool:
        return self.context.should_skip_remote_checks()

    def _expected_env_defaults(self) -> Mapping[str, tuple[str, bool]]:
        return self.context.expected_env_defaults()

    def _build_cdp_client(self) -> Any:
        return self.context.build_cdp_client()

    # ----- Check delegations ------------------------------------------------
    def check_python_version(self) -> bool:
        return check_python_version(self)

    def check_dependencies(self) -> bool:
        return check_dependencies(self)

    def check_environment_variables(self) -> bool:
        return check_environment_variables(self)

    def check_api_connectivity(self) -> bool:
        return check_api_connectivity(self)

    def check_key_permissions(self) -> bool:
        return check_key_permissions(self)

    def check_risk_configuration(self) -> bool:
        return check_risk_configuration(self)

    def check_pretrade_diagnostics(self) -> bool:
        return check_pretrade_diagnostics(self)

    def check_readiness_report(self) -> bool:
        return check_readiness_report(self)

    def check_trade_ideas_readiness(self) -> bool:
        return check_trade_ideas_readiness(self)

    def check_event_store_redaction(self) -> bool:
        return check_event_store_redaction(self)

    def check_test_suite(self) -> bool:
        return check_test_suite(self)

    def check_profile_configuration(self) -> bool:
        return check_profile_configuration(self)

    def check_system_time(self) -> bool:
        return check_system_time(self)

    def check_disk_space(self) -> bool:
        return check_disk_space(self)

    def simulate_dry_run(self) -> bool:
        return simulate_dry_run(self)

    def generate_report(
        self,
        *,
        report_dir: Path | None = None,
        report_path: Path | None = None,
        report_target: ReportTarget = ReportTarget.FILE,
        timestamp: datetime | None = None,
    ) -> tuple[bool, str]:
        return generate_report(
            self,
            report_dir=report_dir,
            report_path=report_path,
            report_target=report_target,
            timestamp=timestamp,
        )
