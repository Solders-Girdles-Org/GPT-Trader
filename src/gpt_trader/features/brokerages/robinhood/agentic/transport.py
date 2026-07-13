"""Typed-only MCP transport for Robinhood Agentic reads and reviews."""

from __future__ import annotations

import webbrowser
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import anyio

from gpt_trader.features.brokerages.robinhood.agentic.errors import (
    RobinhoodAgenticUnavailable,
    RobinhoodAgenticViolation,
)
from gpt_trader.features.brokerages.robinhood.agentic.schemas import (
    ROBINHOOD_AGENTIC_MCP_URL,
    attest_tool_inventory,
)


class RobinhoodAgenticGatewayProtocol(Protocol):
    schema_set_fingerprint: str
    tool_name_fingerprint: str

    async def get_accounts(self) -> dict[str, Any]: ...

    async def get_portfolio(self, account_number: str) -> dict[str, Any]: ...

    async def review_equity_order(self, arguments: Mapping[str, Any]) -> dict[str, Any]: ...

    async def review_option_order(self, arguments: Mapping[str, Any]) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class _McpSessionProtocol(Protocol):
    async def list_tools(self, cursor: str | None) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class KeyringTokenStorage:
    """Store OAuth tokens and dynamic-client metadata in the OS credential store."""

    _SERVICE = "GPT-Trader Robinhood Agentic MCP"

    async def _get(self, key: str) -> str | None:
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RobinhoodAgenticUnavailable(
                "Install the robinhood-agentic extra to use Robinhood Agentic access"
            ) from exc
        return await anyio.to_thread.run_sync(keyring.get_password, self._SERVICE, key)

    async def _set(self, key: str, value: str) -> None:
        import keyring

        await anyio.to_thread.run_sync(keyring.set_password, self._SERVICE, key, value)

    async def get_tokens(self) -> Any | None:
        from mcp.shared.auth import OAuthToken

        value = await self._get("oauth-tokens")
        return None if value is None else OAuthToken.model_validate_json(value)

    async def set_tokens(self, tokens: Any) -> None:
        await self._set("oauth-tokens", tokens.model_dump_json())

    async def get_client_info(self) -> Any | None:
        from mcp.shared.auth import OAuthClientInformationFull

        value = await self._get("oauth-client")
        return None if value is None else OAuthClientInformationFull.model_validate_json(value)

    async def set_client_info(self, client_info: Any) -> None:
        await self._set("oauth-client", client_info.model_dump_json())


class _CallbackServer(HTTPServer):
    callback: tuple[str, str | None] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        code = query.get("code", [""])[0]
        state = query.get("state", [None])[0]
        if parsed.path != "/callback" or not code:
            self.send_response(400)
            body = b"Robinhood OAuth callback was invalid."
        else:
            self.server.callback = (code, state)
            self.send_response(200)
            body = b"Robinhood authorization complete. You may close this window."
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _OAuthCallback:
    def __init__(self) -> None:
        try:
            # Dynamic OAuth registration binds the redirect URI, so keep this
            # command-scoped callback stable across token refreshes.
            self._server = _CallbackServer(("127.0.0.1", 8765), _CallbackHandler)
        except OSError as exc:
            raise RobinhoodAgenticUnavailable(
                "Robinhood Agentic OAuth callback port 8765 is unavailable"
            ) from exc
        self._server.timeout = 300.0

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/callback"

    async def open_browser(self, url: str) -> None:
        opened = await anyio.to_thread.run_sync(webbrowser.open, url)
        if not opened:
            raise RobinhoodAgenticUnavailable("Unable to open Robinhood OAuth in a browser")

    async def wait(self) -> tuple[str, str | None]:
        await anyio.to_thread.run_sync(self._server.handle_request)
        if self._server.callback is None:
            raise RobinhoodAgenticUnavailable("Robinhood OAuth callback timed out")
        return self._server.callback

    def close(self) -> None:
        self._server.server_close()


class McpRobinhoodAgenticGateway:
    """Expose four literal operations after exact connection-time attestation."""

    def __init__(
        self,
        *,
        session: _McpSessionProtocol,
        close_transport: Callable[[], Awaitable[None]],
    ) -> None:
        self._session = session
        self._close_transport = close_transport
        self._input_schemas: dict[str, dict[str, Any]] = {}
        self._output_schemas: dict[str, dict[str, Any]] = {}
        self.schema_set_fingerprint = ""
        self.tool_name_fingerprint = ""
        self._closed = False

    @classmethod
    async def connect(
        cls,
        *,
        token_storage: Any | None = None,
    ) -> McpRobinhoodAgenticGateway:
        """Connect to the one canonical endpoint using official MCP OAuth."""
        try:
            import httpx
            from mcp import ClientSession
            from mcp.client.auth import OAuthClientProvider
            from mcp.client.streamable_http import streamable_http_client
            from mcp.shared.auth import OAuthClientMetadata
            from pydantic import AnyUrl
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RobinhoodAgenticUnavailable(
                "Install the robinhood-agentic extra to use Robinhood Agentic access"
            ) from exc

        callback = _OAuthCallback()
        stack = AsyncExitStack()
        try:
            auth = OAuthClientProvider(
                ROBINHOOD_AGENTIC_MCP_URL,
                OAuthClientMetadata(
                    redirect_uris=[AnyUrl(callback.redirect_uri)],
                    token_endpoint_auth_method="none",
                    grant_types=["authorization_code", "refresh_token"],
                    response_types=["code"],
                    scope="internal",
                    client_name="GPT-Trader read/review adapter",
                    software_version="0.1.0",
                ),
                token_storage or KeyringTokenStorage(),
                redirect_handler=callback.open_browser,
                callback_handler=callback.wait,
            )
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(auth=auth, follow_redirects=False, timeout=30.0)
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    ROBINHOOD_AGENTIC_MCP_URL,
                    http_client=http_client,
                    terminate_on_close=True,
                )
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            gateway = cls(session=session, close_transport=stack.aclose)
            await gateway._attest()
            return gateway
        except Exception:
            await stack.aclose()
            raise
        finally:
            callback.close()

    async def _attest(self) -> None:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await self._session.list_tools(cursor)
            for tool in page.tools:
                tools.append(
                    {
                        "name": tool.name,
                        "inputSchema": tool.inputSchema,
                        "outputSchema": tool.outputSchema,
                    }
                )
            cursor = page.nextCursor
            if cursor is None:
                break
            if cursor in seen_cursors:
                raise RobinhoodAgenticViolation(
                    "Robinhood Agentic tool pagination repeated a cursor"
                )
            seen_cursors.add(cursor)
        (
            self._output_schemas,
            self.schema_set_fingerprint,
            self.tool_name_fingerprint,
        ) = attest_tool_inventory(tools)
        self._input_schemas = {
            tool["name"]: dict(tool["inputSchema"])
            for tool in tools
            if tool["name"] in self._output_schemas
        }

    async def _invoke(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name not in self._output_schemas:
            raise RobinhoodAgenticViolation("Robinhood Agentic operation is not accepted")
        if self._closed:
            raise RobinhoodAgenticUnavailable("Robinhood Agentic connection is closed")
        try:
            from jsonschema import validate

            validate(instance=dict(arguments), schema=self._input_schemas[name])
        except Exception as exc:
            raise RobinhoodAgenticViolation(
                f"Robinhood Agentic {name} arguments failed schema validation"
            ) from exc
        result = await self._session.call_tool(name, dict(arguments))
        if result.isError:
            raise RobinhoodAgenticViolation(f"Robinhood Agentic {name} returned an MCP error")
        payload = result.structuredContent
        if not isinstance(payload, dict):
            raise RobinhoodAgenticViolation(
                f"Robinhood Agentic {name} returned no structured evidence"
            )
        try:
            validate(instance=payload, schema=self._output_schemas[name])
        except Exception as exc:
            raise RobinhoodAgenticViolation(
                f"Robinhood Agentic {name} response failed schema validation"
            ) from exc
        return payload

    async def get_accounts(self) -> dict[str, Any]:
        return await self._invoke("get_accounts", {})

    async def get_portfolio(self, account_number: str) -> dict[str, Any]:
        return await self._invoke("get_portfolio", {"account_number": account_number})

    async def review_equity_order(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await self._invoke("review_equity_order", arguments)

    async def review_option_order(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await self._invoke("review_option_order", arguments)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._close_transport()

    async def __aenter__(self) -> McpRobinhoodAgenticGateway:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.close()


__all__ = [
    "KeyringTokenStorage",
    "McpRobinhoodAgenticGateway",
    "RobinhoodAgenticGatewayProtocol",
]
