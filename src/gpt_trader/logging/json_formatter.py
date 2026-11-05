"""Enhanced JSON logging formatter with correlation ID and domain field support."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .correlation import get_log_context

REDACTED = "[REDACTED]"


class StructuredJSONFormatter(logging.Formatter):
    """JSON formatter that includes correlation IDs and domain field support."""

    def __init__(
        self,
        *,
        ensure_ascii: bool = False,
        default: Any = str,
        sort_keys: bool = False,
        timestamp_format: str = "%Y-%m-%dT%H:%M:%S.%fZ",
    ) -> None:
        """Initialize the JSON formatter.

        Args:
            ensure_ascii: Whether to ensure ASCII encoding in JSON output
            default: Default function for JSON serialization of non-serializable objects
            sort_keys: Whether to sort keys in the JSON output
            timestamp_format: Format string for timestamps
        """
        super().__init__()
        self.ensure_ascii = ensure_ascii
        self.default = default
        self.sort_keys = sort_keys
        self.timestamp_format = timestamp_format

        # Thread-local storage for context that might be specific to each log record
        self._thread_local = threading.local()

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON with correlation context.

        Args:
            record: The log record to format

        Returns:
            JSON-formatted log entry as a string
        """
        # Start with the basic log record information
        log_entry: dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": (
                record.funcName if record.funcName is not None else "<module>"
            ),  # Handle None funcName
            "line": record.lineno,
            "thread": record.thread,
            "process": record.process,
        }

        # Add correlation context
        correlation_context = get_log_context()
        if correlation_context:
            log_entry.update(correlation_context)

        # Add exception information if present
        if record.exc_info:
            log_entry["exception"] = self._format_exception(record.exc_info)

        # Add stack trace if present
        if record.stack_info:
            log_entry["stack_trace"] = record.stack_info

        # Extract extra fields, including those from StructuredLogger
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key not in log_entry:  # Avoid overwriting standard fields
                    log_entry[key] = value

        # Handle context_ attributes from original _extract_extra_fields (if still needed)
        # This part of _extract_extra_fields will now become redundant if StructuredLogger always uses 'extra'
        # but leaving it for backward compatibility if any old records are passed directly.
        extra_fields_from_record_dict = self._extract_extra_fields(record)
        if extra_fields_from_record_dict:
            log_entry.update(extra_fields_from_record_dict)

        # Special handling for reserved attributes that might have been added as extra
        for attr in record.__dict__:
            if attr.startswith("context_"):
                # Convert context_ attributes to regular fields
                clean_name = attr[8:]  # Remove "context_" prefix
                if clean_name not in log_entry:
                    log_entry[clean_name] = getattr(record, attr)

        # Redact sensitive data
        log_entry = self._redact_data(log_entry)

        # Serialize to JSON
        try:
            return json.dumps(
                log_entry,
                ensure_ascii=self.ensure_ascii,
                default=None,  # Pass default=None so cls=DecimalEncoder is always used for Decimals
                sort_keys=self.sort_keys,
                cls=DecimalEncoder,
            )
        except (TypeError, ValueError) as exc:
            # Fallback if JSON serialization fails
            fallback_entry = {
                "timestamp": log_entry["timestamp"],
                "level": log_entry["level"],
                "logger": log_entry["logger"],
                "message": f"JSON serialization failed: {exc}",
                "original_message": str(log_entry.get("message", "")),
                "serialization_error": str(exc),
            }
            return json.dumps(
                fallback_entry,
                ensure_ascii=self.ensure_ascii,
                default=self.default,
                sort_keys=self.sort_keys,
            )

    def _redact_data(self, data: Any) -> Any:
        """Recursively redact sensitive values.

        A dict value is redacted when its key looks sensitive (see
        ``_is_sensitive_key``). Lists, tuples, sets and frozensets are walked,
        and a two-item ``(name, value)`` pair whose name looks sensitive, such
        as a ``("Authorization", "Bearer ...")`` header tuple, has its value
        redacted. Sets become lists so the entry still serialises.
        """
        if isinstance(data, dict):
            return {
                k: REDACTED if self._redacts(k, v) else self._redact_data(v)
                for k, v in data.items()
            }
        if isinstance(data, (list, tuple)):
            if len(data) == 2 and self._redacts(data[0], data[1]):
                pair = [data[0], REDACTED]
                return tuple(pair) if isinstance(data, tuple) else pair
            items = [self._redact_data(item) for item in data]
            return tuple(items) if isinstance(data, tuple) else items
        if isinstance(data, (set, frozenset)):
            return [self._redact_data(item) for item in data]
        return data

    def _redacts(self, key: Any, value: Any) -> bool:
        """Return whether ``value`` stored under ``key`` must be redacted."""
        if not isinstance(key, str):
            return False
        lowered = key.lower()
        if lowered in self.SENSITIVE_KEYS:
            return True
        if value is None or isinstance(value, bool):
            # A flag such as ``cdp_credentials_present`` cannot carry a secret.
            return False
        return self._is_sensitive_key(lowered)

    @classmethod
    def _is_sensitive_key(cls, lowered_key: str) -> bool:
        """Match a lowercased key on credential fragments, ``-`` read as ``_``."""
        normalised = lowered_key.replace("-", "_")
        return any(fragment in normalised for fragment in cls.SENSITIVE_KEY_FRAGMENTS)

    # Exact names, always redacted whatever the value.
    SENSITIVE_KEYS = {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credentials",
        "key_name",
        "passphrase",
        "password",
        "private_key",
        "privatekey",
        "privatekeypem",
        "secret",
        "token",
    }

    # Any key containing one of these (after lowercasing and mapping ``-`` to
    # ``_``) is redacted unless its value is None or a bool. Over-redaction,
    # such as an LLM ``input_tokens`` count, is the accepted cost.
    SENSITIVE_KEY_FRAGMENTS = (
        "secret",
        "token",
        "password",
        "passphrase",
        "private",
        "api_key",
        "apikey",
        "access_key",
        "authorization",
        "cookie",
        "credential",
    )

    def _format_timestamp(self, created: float) -> str:
        """Format a timestamp from the log record.

        Args:
            created: Unix timestamp from the log record

        Returns:
            Formatted timestamp string
        """
        dt = datetime.fromtimestamp(created, UTC)
        return dt.strftime(self.timestamp_format)

    def _format_exception(self, exc_info: Any) -> dict[str, Any]:
        """Format exception information for JSON output.

        Args:
            exc_info: Exception info tuple from the log record

        Returns:
            Dictionary with formatted exception information
        """
        if not exc_info:
            return {}

        exc_type, exc_value, exc_traceback = exc_info

        return {
            "type": exc_type.__name__ if exc_type else "Unknown",
            "message": str(exc_value) if exc_value else "",
            "module": getattr(exc_type, "__module__", "") if exc_type else "",
            # Note: We don't include the full traceback by default as it can be verbose
            # but could be added if needed
        }

    def _extract_extra_fields(self, record: logging.LogRecord) -> dict[str, Any]:
        """Extract extra fields from the log record.

        Args:
            record: The log record to extract extra fields from

        Returns:
            Dictionary of extra fields
        """
        extra_fields = {}

        # Standard attributes that should not be included as extra fields
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "asctime",
        }

        # Extract any additional attributes
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                # Skip context_ attributes as they're handled separately
                if not key.startswith("context_"):
                    extra_fields[key] = value

        return extra_fields


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal objects."""

    def default(self, o: Any) -> Any:
        """Handle Decimal objects by converting them to float or string.

        Args:
            o: Object to encode

        Returns:
            JSON-serializable representation of the object
        """
        if isinstance(o, Decimal):
            # For very large or very small decimals, use string representation
            # to avoid precision loss
            if o.adjusted() > 15 or o.adjusted() < -15:
                return str(o)
            return float(o)
        return super().default(o)


class StructuredJSONFormatterWithTimestamp(StructuredJSONFormatter):
    """JSON formatter with additional timestamp fields for better log analysis."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record with additional timestamp fields.

        Args:
            record: The log record to format

        Returns:
            JSON-formatted log entry as a string
        """
        # Get the base formatted entry
        json_str = super().format(record)

        try:
            # Parse the JSON to add additional fields
            log_entry = json.loads(json_str)

            # Add additional timestamp fields
            created_dt = datetime.fromtimestamp(record.created, UTC)
            log_entry.update(
                {
                    "unix_timestamp": record.created,
                    "date": created_dt.strftime("%Y-%m-%d"),
                    "time": created_dt.strftime("%H:%M:%S"),
                    "timezone": "UTC",
                }
            )

            # Re-serialize with additional fields
            return json.dumps(
                log_entry,
                ensure_ascii=self.ensure_ascii,
                default=self.default,
                sort_keys=self.sort_keys,
                cls=DecimalEncoder,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            # If anything goes wrong, return the original
            return json_str
