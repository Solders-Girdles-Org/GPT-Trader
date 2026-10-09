"""Feature seed helpers for telemetry-derived backlog candidates."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_SEED_REASON_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:\\-]{0,63}$")
_SEED_REASON_STRIP_PATTERN = re.compile(r"[\(\[\{].*?[\)\]\}]+")
_SEED_REASON_MAX_TOKENS = 6
_DEFAULT_SUFFIX_LENGTH = 8
_SEED_PREFIX_TRANSLATION = {
    "a": "g",
    "b": "h",
    "c": "i",
    "d": "j",
    "e": "k",
    "f": "l",
    "2": "m",
    "3": "n",
    "4": "p",
    "5": "q",
    "6": "r",
    "7": "s",
}


@dataclass(frozen=True)
class FeatureSeed:
    """Stable seed identifier and title for backlog candidates."""

    key: str
    title: str


def _slugify(value: str) -> str:
    normalized = _SLUG_PATTERN.sub("-", value.strip().lower())
    normalized = normalized.strip("-")
    return normalized or "seed"


def _hash_signature(signature: Mapping[str, Any]) -> bytes:
    payload = json.dumps(signature, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).digest()


def _encode_seed_suffix(digest: bytes, length: int) -> str:
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    suffix = encoded[:length]
    if suffix:
        replacement = _SEED_PREFIX_TRANSLATION.get(suffix[0])
        if replacement:
            suffix = f"{replacement}{suffix[1:]}"
    return suffix


def summarize_seed_reason(reason: str | None) -> str | None:
    """Return a low-noise reason label suitable for seed titles."""
    if reason is None:
        return None
    reason_text = str(reason).strip()
    if not reason_text:
        return None
    if not _SEED_REASON_PATTERN.match(reason_text):
        if not re.search(r"[A-Za-z]", reason_text):
            return None
        if not any(marker in reason_text for marker in (":", "_", "-")) and re.search(
            r"\s", reason_text
        ):
            return None
        normalized = reason_text.lower()
        normalized = _SEED_REASON_STRIP_PATTERN.sub(" ", normalized)
        normalized = re.sub(r"[^a-z0-9_:\\-]+", "_", normalized)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        normalized = re.sub(r":_+", ":", normalized)
        normalized = re.sub(r"_+:", ":", normalized)
        token_count = 0
        segments: list[str] = []
        for segment in normalized.split(":"):
            if token_count >= _SEED_REASON_MAX_TOKENS:
                break
            tokens: list[str] = []
            for token in re.split(r"[_-]+", segment):
                if not token or not re.search(r"[a-z]", token):
                    continue
                tokens.append(token)
                token_count += 1
                if token_count >= _SEED_REASON_MAX_TOKENS:
                    break
            if tokens:
                segments.append("_".join(tokens))
        if not segments:
            return None
        candidate = ":".join(segments).strip("_:-")
        if not candidate or not _SEED_REASON_PATTERN.match(candidate):
            return None
        return candidate
    return reason_text.lower()


def build_feature_seed(
    title: str,
    *,
    signature: Mapping[str, Any] | None = None,
    suffix_length: int = _DEFAULT_SUFFIX_LENGTH,
) -> FeatureSeed:
    """Build a deterministic seed key and collision-resistant title.

    The suffix is derived from a hash of the title and signature, and is
    appended as a plain token with a non-hex prefix so it survives
    merged-title normalization.
    """

    base_title = title.strip() or "feature"
    normalized = _slugify(base_title)
    payload: dict[str, Any] = {"title": base_title}
    if signature:
        payload["signature"] = signature
    suffix_length = max(4, min(12, int(suffix_length)))
    digest = _hash_signature(payload)
    suffix = _encode_seed_suffix(digest, suffix_length)
    return FeatureSeed(
        key=f"{normalized}-{suffix}",
        title=f"{base_title} seed-{suffix}",
    )
