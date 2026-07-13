# GPT-Trader Approved Abbreviations

---
status: current
---

This glossary lists abbreviations approved for use in the GPT-Trader codebase.
Any abbreviation not listed here should be spelled out in full.

## Approved Abbreviations

| Abbreviation | Full Form | Context | Notes |
|--------------|-----------|---------|-------|
| `API` | Application Programming Interface | All | Industry standard |
| `CLI` | Command Line Interface | All | Industry standard |
| `JSON` | JavaScript Object Notation | All | Industry standard |
| `YAML` | YAML Ain't Markup Language | All | Industry standard |
| `HTTP` | Hypertext Transfer Protocol | All | Industry standard |
| `REST` | Representational State Transfer | All | Industry standard |
| `URL` | Uniform Resource Locator | All | Industry standard |
| `ID` | Identifier | All | Industry standard |
| `UUID` | Universally Unique Identifier | All | Industry standard |
| `PnL` | Profit and Loss | Trading domain | Trading standard |
| `BTC` | Bitcoin | Trading domain | Cryptocurrency ticker |
| `ETH` | Ethereum | Trading domain | Cryptocurrency ticker |
| `USD` | US Dollar | Trading domain | Currency code |
| `USDC` | USD Coin | Trading domain | Stablecoin ticker |
| `WS` | WebSocket | Networking | Common abbreviation |
| `CDP` | Coinbase Developer Platform | Coinbase API | Coinbase-specific |
| `INTX` | Coinbase International Exchange | Coinbase API | Coinbase-specific |
| `JWT` | JSON Web Token | Security | Authentication standard |
| `MCP` | Model Context Protocol | Agent integrations | Standard protocol for connecting AI applications to external tools and data |
| `MVP` | Minimum Viable Product | Project management | Industry standard |
| `PR` | Pull Request | Development | Git/GitHub standard |
| `CI` | Continuous Integration | Development | Industry standard |
| `CD` | Continuous Deployment | Development | Industry standard |
| `E2E` | End-to-End | Testing | Industry standard |
| `qty` | Quantity | Trading domain | Standard trading term for order/position size |

## Domain Term Distinctions

Risk-limit fields that read alike but measure different things (from the
accepted decision `docs/decisions/canonical-risk-limit-vocabulary.md`):

| Term | Measures | Not to be confused with |
|------|----------|-------------------------|
| `max_loss_per_idea_pct` | **Loss at invalidation** — the equity percentage lost if the idea's stop is hit | Notional exposure: a small stop distance lets a large position stay inside this cap |
| `max_position_pct_per_symbol` | **Notional exposure** — the equity percentage a symbol's position may occupy | Loss at invalidation: exposure says nothing about how much is lost at the stop |

Percentage unit suffixes (`_pct` = percent points, `_fraction` = unit
fraction) are defined in `docs/naming.md` §2.4.

## Banned Abbreviations

These abbreviations are banned and must be spelled out:

| Banned | Use Instead | Rationale |
|--------|-------------|-----------|
| `cfg` | `config` | Clarity over brevity |
| `svc` | `service` | Clarity over brevity |
| `mgr` | `manager` | Clarity over brevity |
| `util` / `utils` | `utilities` | Clarity over brevity |
| `amt` | `amount` | Clarity over brevity |
| `calc` | `calculate` | Clarity over brevity |
| `upd` | `update` | Clarity over brevity |
| `Impl` | (describe the implementation) | Avoid generic suffixes |

## Exception Process

To add a new approved abbreviation:

1. Open a PR with the proposed addition to this glossary
2. Include rationale for why the abbreviation improves clarity
3. Require maintainer approval
4. If the abbreviation was previously banned, update `config/agents/naming_patterns.yaml`

## Inline Exceptions

For legacy code or external API compatibility, use `# naming: allow` comments:

```python
# External API field - cannot rename
response_qty = api_response["qty"]  # naming: allow
```

## Enforcement

Naming standards are enforced via:
- **Pre-commit hook**: Blocks commits with violations through the `naming-check` hook (use `SKIP=naming-check git commit` to bypass)
- **Agent command**: Run `uv run agent-naming --strict --quiet` for explicit strict-scan proof, or `uv run agent-naming` for the default local scan
- **Code review**: Reviewers flag deviations

See `docs/naming.md` for complete naming conventions.
