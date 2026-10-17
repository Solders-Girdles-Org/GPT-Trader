"""Anthropic model transport for the AI trade manager, behind a hard spend ceiling.

Before each call the transport prices the request at its worst case (an upper
bound on input tokens plus the pinned ``max_tokens`` of output) and reserves
that amount in a spend ledger, refusing loudly when the reservation would pass
the cap. The ledger is a file of its own, outside any run's journal, so a call
whose bar never commits (a crash between the call and the journal commit
re-asks for that bar) stays counted. A reply settles its reservation at the
measured cost; a failure whose billing is unknown keeps the full reservation.
A reply costing more than its reservation, coming from a model other than the
pinned one, or reporting a service tier, speed or region outside the price
table, stops the run.

The request is pinned to the input's manager settings (model, effort and
``max_tokens``, which the round-two protocol freezes) and asks for structured
output in the reply shape ``parse_reply`` accepts. No refusal fallback is
requested: a fallback would answer with a different model than the one under
test. The credential comes from the environment and is never journaled.

Nothing here runs without an explicit opt-in; tests inject a stub client.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from gpt_trader.features.experiment.inputs import ExperimentInput, ManagerSettings
from gpt_trader.features.experiment.ledger import encode, initial_state
from gpt_trader.features.experiment.trade_manager import (
    TransportError,
    TransportReply,
)

CREDENTIAL_ENV = "ANTHROPIC_API_KEY"
# USD per million tokens (input, output), from Anthropic's API pricing read
# 2026-10-17. A model without a recorded price is never called.
PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "claude-fable-5-1": (Decimal("10"), Decimal("50")),
}
# Cache writes cost 1.25 times base input (two times for one-hour writes). The
# transport sets no cache_control, so cache counts should stay zero; any that
# appear are charged in full, and a cost above its reservation stops the run.
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
HOUR_CACHE_WRITE_MULTIPLIER = Decimal("2")
# What a reply's usage must report for PRICING to hold; None means not reported.
PRICED_USAGE = {
    "service_tier": (None, "standard"),
    "speed": (None, "standard"),
    "inference_geo": (None, "global"),
}
# A token covers at least one UTF-8 byte, so the request's bytes bound its
# input tokens. This allowance covers what the API adds around the request
# (role markers and the output schema's grammar prompt), generously.
OVERHEAD_TOKENS = 2000
TIMEOUT_SECONDS = 600.0
BASE_URL = "https://api.anthropic.com"
# Marks a SQLite file as a spend ledger ("GTSP"), so no other file is adopted.
APPLICATION_ID = 0x47545350
# Client errors a later turn may not repeat; any other client error stops the run.
TRANSIENT_CLIENT_STATUSES = frozenset({408, 409, 429})
# Client errors and overload (529) are rejected unbilled; another server error
# may have been billed, so it keeps its reservation.
OVERLOADED_STATUS = 529

REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "enter", "exit"]},
        "reason": {"type": "string"},
        "rulebook": {"type": "string"},
        "size_fraction": {"type": "string"},
        "stop": {"type": "string"},
        "target": {"type": "string"},
        "expires_in_hours": {"type": "integer"},
    },
    "required": ["action", "reason"],
    "additionalProperties": False,
}


class SpendCeilingReached(Exception):
    """The next call could pass the spend cap; the run stops before sending it."""


class TransportHalted(Exception):
    """A failure no later turn would avoid (credential, model or request); the run stops."""


class SpendLedgerError(ValueError):
    """The spend ledger cannot be used as given."""


def wire_params(request: dict[str, Any], manager: ManagerSettings) -> dict[str, Any]:
    """The Messages API arguments for one decision request; thinking is always on."""
    return {
        "model": manager.model,
        "max_tokens": manager.max_tokens,
        "system": request["instructions"],
        "messages": [{"role": "user", "content": encode(request["context"])}],
        # Priority capacity and US-only inference are priced differently; PRICING
        # records standard capacity at global inference.
        "service_tier": "standard_only",
        "inference_geo": "global",
        "output_config": {
            "effort": manager.effort,
            "format": {"type": "json_schema", "schema": REPLY_SCHEMA},
        },
    }


def input_token_bound(params: dict[str, Any]) -> int:
    """An upper bound on the call's input tokens: request bytes plus the overhead."""
    sent = [params["system"], encode(params["output_config"]["format"])]
    sent.extend(message["content"] for message in params["messages"])
    return sum(len(text.encode("utf-8")) for text in sent) + OVERHEAD_TOKENS


def pricing_for(model: str) -> tuple[Decimal, Decimal]:
    try:
        return PRICING[model]
    except KeyError:
        raise TransportHalted(
            f"No recorded price for model {model}; add it to PRICING before any call"
        ) from None


def worst_case_usd(params: dict[str, Any]) -> Decimal:
    input_price, output_price = pricing_for(params["model"])
    max_tokens: int = params["max_tokens"]
    return (
        input_token_bound(params) * input_price * CACHE_WRITE_MULTIPLIER + max_tokens * output_price
    ) / 1_000_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SpendLedger:
    """Every call's charge under one cap, shared by all runs that name the file.

    A call counts at its settled cost once replied, else at its reservation.
    The file is created by the first reservation, marked as a spend ledger and
    bound to its cap; any other existing file is refused, never written.
    """

    def __init__(self, path: Path, cap_usd: Decimal) -> None:
        if not isinstance(cap_usd, Decimal) or not cap_usd.is_finite() or cap_usd <= 0:
            raise SpendLedgerError("Spend cap must be a positive dollar amount")
        self.path = path
        self.cap_usd = cap_usd
        if path.is_dir() or not path.parent.is_dir():
            raise SpendLedgerError(f"Spend ledger {path} must be a file in an existing directory")
        if self._exists():
            # Read-write without creating, so an interrupted write's journal is
            # recovered rather than mistaken for a foreign file.
            uri = f"{path.resolve().as_uri()}?mode=rw"
            try:
                with closing(sqlite3.connect(uri, uri=True, timeout=30)) as connection:
                    self._check(connection)
            except sqlite3.OperationalError as error:
                raise SpendLedgerError(f"Could not read spend ledger {path}: {error}") from None
            except sqlite3.DatabaseError:
                raise SpendLedgerError(
                    f"{path} is not a readable spend ledger. If it is this cap's ledger, it is "
                    "damaged: recover it, since a new file would restart the count at $0"
                ) from None

    def _exists(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size > 0

    def _check(self, connection: sqlite3.Connection) -> None:
        """Refuse a file that is not a spend ledger bound to this cap."""
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise SpendLedgerError(f"{self.path} is not a spend ledger; name a new file")
        row = connection.execute("SELECT cap_usd FROM ceiling WHERE id = 1").fetchone()
        if row is None:
            raise SpendLedgerError(f"Spend ledger {self.path} has no cap; name a new file")
        (cap,) = row
        if Decimal(cap) != self.cap_usd:
            raise SpendLedgerError(
                f"Spend ledger {self.path} is bound to a ${cap} cap, not ${self.cap_usd}; "
                "a new cap needs a new ledger"
            )

    def _initialize(self, connection: sqlite3.Connection) -> None:
        """Inside the reservation's transaction: create a new ledger, else check it."""
        tables = connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        if tables:
            self._check(connection)
            return
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute(
            "CREATE TABLE ceiling (id INTEGER PRIMARY KEY CHECK (id = 1), "
            "cap_usd TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "model TEXT NOT NULL, reserved_at TEXT NOT NULL, reserved_usd TEXT NOT NULL, "
            "status TEXT NOT NULL, cost_usd TEXT, detail TEXT)"
        )
        connection.execute("INSERT INTO ceiling VALUES (1, ?, ?)", (str(self.cap_usd), _now()))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    @contextmanager
    def _during_run(self) -> Iterator[sqlite3.Connection]:
        """A ledger that cannot be read or written stops the run; a reservation stays held."""
        try:
            with closing(self._connect()) as connection:
                yield connection
        except sqlite3.Error as error:
            raise TransportHalted(f"Spend ledger {self.path} is unusable: {error}") from None

    @staticmethod
    def _charged(connection: sqlite3.Connection) -> Decimal:
        rows = connection.execute("SELECT reserved_usd, cost_usd FROM calls").fetchall()
        return sum((Decimal(cost if cost is not None else held) for held, cost in rows), Decimal(0))

    def spent_usd(self) -> Decimal:
        if not self._exists():
            return Decimal(0)
        with closing(self._connect()) as connection:
            return self._charged(connection)

    def calls(self) -> list[dict[str, Any]]:
        if not self._exists():
            return []
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM calls ORDER BY id")]

    def reserve(self, model: str, amount: Decimal) -> int:
        """Count ``amount`` before a call, or raise if it could pass the cap."""
        if amount > self.cap_usd and not self._exists():
            raise SpendCeilingReached(
                f"Spend ceiling reached: one call could cost up to ${amount}, above the "
                f"${self.cap_usd} cap; it was not sent"
            )
        with self._during_run() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._initialize(connection)
            except SpendLedgerError as error:
                connection.execute("ROLLBACK")
                raise TransportHalted(str(error)) from None
            charged = self._charged(connection)
            if charged + amount > self.cap_usd:
                connection.execute("ROLLBACK")
                raise SpendCeilingReached(
                    f"Spend ceiling reached: ${charged} of the ${self.cap_usd} cap is spent or "
                    f"reserved and the next call could cost up to ${amount}; it was not sent"
                )
            cursor = connection.execute(
                "INSERT INTO calls (model, reserved_at, reserved_usd, status) "
                "VALUES (?, ?, ?, 'reserved')",
                (model, _now(), str(amount)),
            )
            connection.execute("COMMIT")
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def settle(self, call: int, cost_usd: Decimal | None, status: str, detail: str = "") -> None:
        """Record a call's outcome; ``None`` keeps the reservation as its charge."""
        with self._during_run() as connection:
            connection.execute(
                "UPDATE calls SET status = ?, cost_usd = ?, detail = ? WHERE id = ?",
                (status, None if cost_usd is None else str(cost_usd), detail, call),
            )


class AnthropicTransport:
    """One Messages API call per decision turn, counted against the spend ledger."""

    def __init__(self, manager: ManagerSettings, ledger: SpendLedger, client: Any) -> None:
        pricing_for(manager.model)
        self.manager = manager
        self.model = manager.model
        self.ledger = ledger
        self.client = client

    def decide(self, request: dict[str, Any]) -> TransportReply:
        import anthropic

        params = wire_params(request, self.manager)
        input_price, output_price = pricing_for(self.model)
        reserved = worst_case_usd(params)
        call = self.ledger.reserve(self.model, reserved)
        started = time.monotonic()
        try:
            response = self.client.messages.create(**params)
        except anthropic.APITimeoutError:
            self.ledger.settle(call, None, "timeout")
            raise TransportError(f"request timed out after {TIMEOUT_SECONDS:g}s") from None
        except anthropic.APIConnectionError as error:
            self.ledger.settle(call, None, "connection error", str(error))
            raise TransportError(f"connection error: {error}") from None
        except anthropic.APIStatusError as error:
            status = error.status_code
            unbilled = status < 500 or status == OVERLOADED_STATUS
            self.ledger.settle(call, Decimal(0) if unbilled else None, f"http {status}", str(error))
            if status < 500 and status not in TRANSIENT_CLIENT_STATUSES:
                raise TransportHalted(
                    f"API rejected the request (HTTP {status}): {error}"
                ) from None
            raise TransportError(f"API error (HTTP {status}): {error}") from None
        latency_ms = int((time.monotonic() - started) * 1000)
        usage = response.usage
        cache_write = usage.cache_creation_input_tokens or 0
        cache_read = usage.cache_read_input_tokens or 0
        hour_writes = getattr(usage.cache_creation, "ephemeral_1h_input_tokens", 0) or 0
        reply = TransportReply(
            model=response.model,
            text="".join(block.text for block in response.content if block.type == "text"),
            stop_reason=response.stop_reason or "",
            input_tokens=usage.input_tokens + cache_write + cache_read,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
            input_usd_per_million=input_price,
            output_usd_per_million=output_price,
        )
        surcharge = (
            (cache_write - hour_writes) * (CACHE_WRITE_MULTIPLIER - 1)
            + hour_writes * (HOUR_CACHE_WRITE_MULTIPLIER - 1)
        ) * input_price
        cost = reply.cost_usd + surcharge / 1_000_000
        unpriced = {
            name: getattr(usage, name, None)
            for name, priced in PRICED_USAGE.items()
            if getattr(usage, name, None) not in priced
        }
        # Spend billed outside the price table, or for another model, is unknown:
        # keep the reservation.
        foreign = reply.model != self.model
        charged = max(cost, reserved) if unpriced or foreign else cost
        self.ledger.settle(call, charged, "replied", reply.stop_reason)
        if foreign:
            raise TransportHalted(
                f"Reply came from model {reply.model}, not the pinned {self.model}; "
                "the turn is not evidence for the model under test"
            )
        if unpriced:
            raise TransportHalted(
                f"Reply usage reports {unpriced}, which the price table does not cover; "
                "its spend is not known"
            )
        if cost > reserved:
            raise TransportHalted(
                f"Measured cost ${cost} exceeded the call's ${reserved} reservation; "
                "the spend bound no longer holds"
            )
        return reply


def from_environment(
    manager: ManagerSettings, ledger: SpendLedger, environ: Mapping[str, str] = os.environ
) -> AnthropicTransport:
    """The real transport: the official SDK, no SDK retries, credential from the environment."""
    key = environ.get(CREDENTIAL_ENV, "")
    if not key:
        raise TransportHalted(f"{CREDENTIAL_ENV} is not set; no model call can be made")
    try:
        import anthropic
    except ImportError:
        raise TransportHalted(
            "The Anthropic SDK is not installed; run uv sync --extra ai-trade-manager"
        ) from None
    # These would redirect the credential or change requests outside this module.
    for name in ("ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_BASE_URL"):
        if environ.get(name) is not None or os.environ.get(name) is not None:
            raise TransportHalted(f"{name} is set; unset it before any model call")
    # Each retry would be another billed request outside the ledger, so none are made.
    client = anthropic.Anthropic(
        api_key=key, base_url=BASE_URL, max_retries=0, timeout=TIMEOUT_SECONDS
    )
    return AnthropicTransport(manager, ledger, client)


def parse_cap(raw: str) -> Decimal:
    try:
        cap = Decimal(raw)
    except InvalidOperation:
        raise SpendLedgerError(f"{raw!r} is not a dollar amount") from None
    if not cap.is_finite() or cap <= 0:
        raise SpendLedgerError("Spend cap must be a positive dollar amount")
    return cap


class _HoldAndMeasure:
    """Always holds, recording each request's size; never calls a model."""

    model = "offline-estimate"

    def __init__(self, manager: ManagerSettings) -> None:
        self.manager = manager
        self.bounds: list[int] = []
        self.contexts: list[int] = []

    def decide(self, request: dict[str, Any]) -> TransportReply:
        params = wire_params(request, self.manager)
        self.bounds.append(input_token_bound(params))
        self.contexts.append(len(params["messages"][0]["content"].encode("utf-8")))
        text = '{"action":"hold","reason":"offline estimate"}'
        return TransportReply(self.model, text, "end_turn", 0, 0, 0, Decimal(0), Decimal(0))


def estimate_spend(source: ExperimentInput) -> dict[str, Any]:
    """Offline request sizes and worst-case cost for one run: no client, key or call.

    Replays the input with a transport that always holds, so it counts the
    cadence turns of a run that never trades; each entry fill and each exit
    adds one turn more. Sizes are measured from the requests W1 builds.
    """
    from gpt_trader.features.experiment.engine import advance

    if source.manager is None:
        raise ValueError("Spend estimates apply only to the AI trade manager channel")
    input_price, output_price = pricing_for(source.manager.model)
    state = initial_state(source)
    measure = _HoldAndMeasure(source.manager)
    bounds, contexts = measure.bounds, measure.contexts
    for sequence in range(len(source.candles)):
        state = advance(source, sequence, state, measure)["state"]
    turns = len(bounds)
    worst = (
        sum(bounds) * input_price * CACHE_WRITE_MULTIPLIER
        + turns * source.manager.max_tokens * output_price
    ) / 1_000_000
    return {
        "symbol": source.symbol,
        "model": source.manager.model,
        "effort": source.manager.effort,
        "max_tokens": source.manager.max_tokens,
        "turns_without_fills": turns,
        "context_bytes_max": max(contexts, default=0),
        "input_token_bound_max": max(bounds, default=0),
        "input_token_bound_mean": round(sum(bounds) / turns) if turns else 0,
        "worst_case_usd": str(worst.quantize(Decimal("0.01"))),
        "pricing_usd_per_million": {"input": str(input_price), "output": str(output_price)},
    }
