from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PLACEHOLDER = "-"


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _sanitize_text(value: str) -> str:
    return " ".join(value.splitlines()).strip()


def _prepare_timestamp_text(value: str) -> str:
    text = value.replace(" ", "T")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return text


def parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = _sanitize_text(str(value))
            if not text:
                return None
            parsed = datetime.fromisoformat(_prepare_timestamp_text(text))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    except Exception:
        return None


def format_timestamp(
    value: str | datetime | None,
    *,
    placeholder: str = DEFAULT_PLACEHOLDER,
) -> str:
    try:
        if value is None:
            return placeholder
        if isinstance(value, datetime):
            parsed = value
        else:
            text = _sanitize_text(str(value))
            if not text:
                return placeholder
            parsed = parse_timestamp(text)
            if parsed is None:
                return text

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.isoformat()
    except Exception:
        return placeholder


def format_status_line(
    name: str,
    value: Any,
    *,
    placeholder: str = DEFAULT_PLACEHOLDER,
) -> str:
    try:
        text = _coerce_text(value)
        if text is None:
            text = ""
        text = _sanitize_text(text)
        if not text:
            text = placeholder
        return f"{name}={text}"
    except Exception:
        return f"{name}={placeholder}"


def format_reason_codes(codes: Sequence[str]) -> str:
    text = ",".join(str(code) for code in codes if code)
    return format_status_line("reason_codes", text)
