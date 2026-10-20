"""
WebSocket transport implementations for Coinbase.

Provides both real and mock transports for production and testing.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

from gpt_trader.utilities.logging_patterns import get_logger

try:  # pragma: no cover - optional dependency shim
    import websocket as websocket  # type: ignore[import-not-found, assignment]

    _HAS_WEBSOCKET = True
except ImportError:  # pragma: no cover - fallback stub for tests

    class _WebsocketStub:
        WebSocketException = Exception

        @staticmethod
        def enableTrace(_flag: bool) -> None:
            raise ModuleNotFoundError(
                "websocket-client is not installed. Install with "
                "`pip install gpt-trader[live-trade]` or `uv sync`."
            )

        @staticmethod
        def create_connection(*_args: Any, **_kwargs: Any) -> Any:
            raise ModuleNotFoundError(
                "websocket-client is not installed. Install with "
                "`pip install gpt-trader[live-trade]` or `uv sync`."
            )

    websocket = _WebsocketStub()  # type: ignore[assignment]
    sys.modules.setdefault("websocket", websocket)  # type: ignore[arg-type]  # allow patch
    _HAS_WEBSOCKET = False

if TYPE_CHECKING:
    from websocket import WebSocket

    from gpt_trader.app.config import BotConfig

_TRUTHY = {"1", "true", "yes", "on"}

logger = get_logger(__name__, component="coinbase_transport")


class RealTransport:
    """Real WebSocket transport using websocket-client library."""

    def __init__(self, *, config: BotConfig | None = None) -> None:
        self.ws: WebSocket | None = None
        self.url: str | None = None
        self._config = config

    def connect(
        self, url: str | dict[str, str] | None = None, headers: dict[str, str] | None = None
    ) -> None:
        """Connect to the WebSocket server with optional headers."""

        if isinstance(url, dict) and headers is None:
            headers = url
            url = self.url

        if url is not None and isinstance(url, str):
            self.url = url

        if self.url is None:
            raise ValueError("WebSocket URL is required for connection")

        options: dict[str, Any] = {}
        if headers:
            options["header"] = headers

        timeout_raw = os.getenv("COINBASE_WS_CONNECT_TIMEOUT")
        if timeout_raw:
            try:
                options["timeout"] = float(timeout_raw)
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring invalid COINBASE_WS_CONNECT_TIMEOUT=%s (expected float)", timeout_raw
                )

        subprotocols_raw = os.getenv("COINBASE_WS_SUBPROTOCOLS")
        if subprotocols_raw:
            subprotocols = [token.strip() for token in subprotocols_raw.split(",") if token.strip()]
            if subprotocols:
                options["subprotocols"] = subprotocols

        trace_flag = (os.getenv("COINBASE_WS_ENABLE_TRACE") or "").strip().lower()
        if trace_flag in _TRUTHY:
            try:
                websocket.enableTrace(True)  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - optional trace feature
                logger.warning("Unable to enable websocket trace output", exc_info=True)

        try:
            self.ws = websocket.create_connection(self.url, **options)
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "websocket-client is not installed. Install the live trading extras with "
                "`pip install gpt-trader[live-trade]` or `uv sync`."
            ) from exc
        logger.info(f"Connected to WebSocket: {self.url}")

    def disconnect(self) -> None:
        """Disconnect from the WebSocket server."""
        if self.ws:
            try:
                self.ws.close()
                logger.info("Disconnected from WebSocket")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
            finally:
                self.ws = None

    def subscribe(self, message: dict[str, Any]) -> None:
        """Send a subscription message."""
        if not self.ws:
            raise RuntimeError("Not connected to WebSocket")

        msg_str = json.dumps(message)
        self.ws.send(msg_str)
        logger.debug(f"Sent subscription: {msg_str}")

    def stream(self) -> Iterable[dict[str, Any]]:
        """Stream messages from the WebSocket."""
        if not self.ws:
            raise RuntimeError("Not connected to WebSocket")

        while True:
            try:
                msg = self.ws.recv()
            except StopIteration:
                break
            except Exception as e:
                logger.error("WebSocket stream error: %s", e)
                raise

            try:
                data = json.loads(msg)
            except Exception as exc:  # pragma: no cover - bubble parse errors
                logger.error("WebSocket stream error: %s", exc)
                raise
            yield data


class MockTransport:
    """Mock WebSocket transport for testing."""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages: list[dict[str, Any]] = messages or []
        self.connected = False
        self.subscriptions: list[dict[str, Any]] = []
        self.headers: dict[str, Any] | None = None

        self.connect = Mock(side_effect=self._connect_impl)
        self.disconnect = Mock(side_effect=self._disconnect_impl)
        self.subscribe = Mock(side_effect=self._subscribe_impl)
        self.stream = Mock(side_effect=self._stream_impl)
        self.add_message = Mock(side_effect=self._add_message_impl)

    # Internal implementations -------------------------------------------------
    def _connect_impl(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.connected = True
        self.headers = headers
        logger.debug("Mock connected to: %s", url)

    def _disconnect_impl(self) -> None:
        self.connected = False
        logger.debug("Mock disconnected")

    def _subscribe_impl(self, message: dict[str, Any]) -> None:
        self.subscriptions.append(message)
        logger.debug("Mock subscription: %s", message)

    def _stream_impl(self) -> Iterable[dict[str, Any]]:
        yield from self.messages

    def _add_message_impl(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


class NoopTransport:
    """No-op transport used when streaming is explicitly disabled.

    Provides the same interface as RealTransport/MockTransport but does nothing.
    Useful for environments where websocket-client isn't installed or
    tests want to avoid network dependencies without monkeypatching imports.
    """

    def __init__(self, *, config: BotConfig | None = None) -> None:
        self.connected = False
        self._config = config

    def connect(self, url: str, headers: dict[str, str] | None = None) -> None:  # noqa: ARG002
        self.connected = True
        logger.info("NoopTransport connect called (streaming disabled)")

    def disconnect(self) -> None:
        self.connected = False
        logger.info("NoopTransport disconnect called")

    def subscribe(self, message: dict[str, Any]) -> None:  # noqa: ARG002
        logger.debug("NoopTransport subscribe called (ignored)")

    def stream(self) -> Iterable[dict[str, Any]]:
        return iter([])
