"""Helpers for normalizing profile-loading errors."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TypedDict


class ProfileErrorCategory(str, Enum):
    """Brief category for profile-loading failures."""

    YAML_PARSE = "yaml_parse"
    MISSING_FILE = "missing_file"
    UNKNOWN = "unknown"


class ProfileErrorSeverity(str, Enum):
    """Severity levels used when logging profile-loading findings."""

    ERROR = "error"
    WARNING = "warning"


class ProfileErrorPayload(TypedDict):
    """Normalized payload describing a profile-loading failure."""

    profile: str
    path: str | None
    category: str
    severity: str
    reason: str
    remediation: str


def _sanitize_text(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    normalized = value.strip()
    if not normalized:
        return fallback
    return normalized.splitlines()[0]


def _normalize_remediation(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    normalized = value.strip().replace("\n", " ")
    if not normalized:
        return fallback
    return normalized


def _stringify_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    if isinstance(path, Path):
        return path.as_posix()
    return str(path)


def format_profile_error_payload(
    *,
    profile: str,
    category: ProfileErrorCategory,
    severity: ProfileErrorSeverity,
    reason: str,
    remediation: str,
    path: Path | str | None = None,
) -> ProfileErrorPayload:
    """Return a stable payload for profile-loading failures."""

    return {
        "profile": profile,
        "path": _stringify_path(path),
        "category": category.value,
        "severity": severity.value,
        "reason": _sanitize_text(reason, fallback=category.value),
        "remediation": _normalize_remediation(remediation, fallback="Refer to documentation."),
    }


def profile_yaml_parse_error_payload(
    *,
    profile: str,
    path: Path | str | None,
    exception: Exception,
) -> ProfileErrorPayload:
    """Describe a YAML parsing failure for a profile."""

    reason = _sanitize_text(str(exception), fallback="YAML parsing failed")
    remediation = (
        f"Inspect {path or profile} for syntax issues or invalid values and retry."
        " Profile loading is blocked until the file is repaired."
    )
    return format_profile_error_payload(
        profile=profile,
        path=path,
        category=ProfileErrorCategory.YAML_PARSE,
        severity=ProfileErrorSeverity.ERROR,
        reason=reason,
        remediation=remediation,
    )


def profile_yaml_missing_payload(
    *,
    profile: str,
    path: Path | str | None,
) -> ProfileErrorPayload:
    """Describe a missing profile YAML file."""

    reason = f"Profile YAML not found at {path or profile}."
    remediation = f"Create {path or 'config/profiles/<profile>.yaml'} or rely on the shared defaults before retrying."
    return format_profile_error_payload(
        profile=profile,
        path=path,
        category=ProfileErrorCategory.MISSING_FILE,
        severity=ProfileErrorSeverity.WARNING,
        reason=reason,
        remediation=remediation,
    )


__all__ = [
    "ProfileErrorCategory",
    "ProfileErrorSeverity",
    "ProfileErrorPayload",
    "format_profile_error_payload",
    "profile_yaml_missing_payload",
    "profile_yaml_parse_error_payload",
]
