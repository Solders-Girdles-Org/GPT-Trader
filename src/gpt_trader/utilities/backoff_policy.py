"""Deterministic backoff policy helpers."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BackoffDecision:
    """Result of evaluating a backoff attempt."""

    attempt: int
    delay_seconds: float
    capped: bool


def evaluate_backoff_delay(
    *,
    attempt: int,
    base_delay: float,
    max_delay: float,
    multiplier: float = 2.0,
    jitter: float = 0.0,
    random_fn: Callable[[], float] | None = None,
) -> BackoffDecision:
    """Pure evaluation of a retry backoff delay without reading clocks."""

    if attempt <= 1 or base_delay <= 0:
        return BackoffDecision(attempt=attempt, delay_seconds=0.0, capped=False)

    raw_delay = base_delay * (multiplier ** (attempt - 2))
    capped = raw_delay >= max_delay
    delay = min(raw_delay, max_delay)

    if jitter > 0 and delay > 0:
        rng = random_fn or random.random
        delay += delay * jitter * rng()

    return BackoffDecision(attempt=attempt, delay_seconds=float(delay), capped=capped)


def backoff_delay_with_jitter(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
    multiplier: float,
    jitter_pct: float,
) -> float:
    """Exponential backoff delay with symmetric jitter (0-indexed attempts).

    Grows ``base_seconds * multiplier**attempt`` capped at ``max_seconds``,
    then applies ``random.uniform`` jitter of +/- ``jitter_pct`` (floored at
    0.1s) to prevent thundering herd on reconnection.
    """
    delay = base_seconds * (multiplier**attempt)
    delay = min(delay, max_seconds)

    if jitter_pct > 0:
        jitter_range = delay * jitter_pct
        jitter = random.uniform(-jitter_range, jitter_range)
        delay = max(0.1, delay + jitter)

    return delay


__all__ = [
    "BackoffDecision",
    "backoff_delay_with_jitter",
    "evaluate_backoff_delay",
]
