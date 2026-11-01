"""
GPT-Trader - Coinbase-oriented trading system

Trading and operator tooling under active cleanup and migration planning.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from functools import wraps
from types import coroutine as _types_coroutine
from typing import Any

__version__ = "2.0.0"
__author__ = "RJ + GPT-5"

# ---------------------------------------------------------------------------
# Compatibility helpers
# ---------------------------------------------------------------------------
# Python 3.12 removed ``asyncio.coroutine`` – a number of integration fixtures
# (and some legacy application code) still rely on the attribute being present.
# Re-introduce it by aliasing to ``types.coroutine`` when missing.
if not hasattr(asyncio, "coroutine"):  # pragma: no cover - compatibility shim

    def _asyncio_coroutine(func: Callable[..., Any]) -> Callable[..., Awaitable[Any] | Any]:
        if inspect.isgeneratorfunction(func):
            return _types_coroutine(func)
        if inspect.iscoroutinefunction(func):
            return func

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Awaitable[Any]:
            async def _async_adapter() -> Any:
                result = func(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                if inspect.isgenerator(result):
                    return await _types_coroutine(lambda: result)()
                return result

            return _async_adapter()

        return wrapper

    asyncio.coroutine = _asyncio_coroutine  # type: ignore[attr-defined]

from . import cli

__all__ = [
    "__version__",
    "__author__",
    "cli",
]
