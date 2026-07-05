# CLI Conventions

---
status: current
---

Canonical conventions for `gpt-trader` CLI commands. Use repo-native commands
(`uv run gpt-trader ...`, `uv run agent-*`) in docs and examples — not editor
slash commands.

## Command hierarchy

- **Noun-first** for grouped operations under a domain: `gpt-trader broker test`,
  `gpt-trader account balance`, `gpt-trader strategy run`.
- **Verb-first** for standalone actions: `gpt-trader run`, `gpt-trader validate`,
  `gpt-trader preflight`.

## Human-readable output

Simple status commands use a leading glyph and stable phrasing:

- Success: `✓ <Service> <action> OK (<details>)` — exit code `0`.
- Failure: `✗ <Service> <action> FAILED: <error>` — exit code `1`.

Exit codes are `0` for success and `1` for failure. See
[`coinbase.py`](../../src/gpt_trader/cli/commands/coinbase.py) (`connectivity OK
(...)` / `FAILED: ...`) and [`ideas.py`](../../src/gpt_trader/cli/commands/ideas.py)
for live examples.

## Structured output (`--output-format json`)

Commands that support programmatic use accept `--output-format json` and emit the
[`CliResponse`](../../src/gpt_trader/cli/response.py) envelope:

```json
{
  "success": true,
  "exit_code": 0,
  "command": "optimize list",
  "data": {},
  "errors": [],
  "warnings": [],
  "metadata": {"timestamp": "...", "was_noop": false, "version": "1.0"}
}
```

Build responses with the factory methods rather than by hand:

- `CliResponse.success_response(command, data=..., warnings=..., was_noop=...)`
- `CliResponse.error_response(command, code, message, details=...)`

Errors carry a machine-readable
[`CliErrorCode`](../../src/gpt_trader/cli/response.py) (serialized as a plain
string), e.g. `CONFIG_INVALID`, `RUN_NOT_FOUND`. In tests, compare against the
enum value:

```python
assert response.errors[0].code == CliErrorCode.CONFIG_INVALID.value
assert response.success is True
assert response.data["key"] == expected_value
```

Use [`RawCliOutput`](../../src/gpt_trader/cli/response.py) when a command must emit
its content exactly as provided, bypassing the envelope.

## Worked example

The trade-idea CLI applies these conventions end to end; see
[`TRADE_IDEA_CLI_SPEC.md`](../specs/TRADE_IDEA_CLI_SPEC.md).

## Ownership

This doc owns the *general* CLI response/output contract. Command-family specs
(e.g. [TRADE_IDEA_CLI_SPEC.md](../specs/TRADE_IDEA_CLI_SPEC.md)) are
self-canonical for their own surface and should reference this doc rather than
restate the envelope.
