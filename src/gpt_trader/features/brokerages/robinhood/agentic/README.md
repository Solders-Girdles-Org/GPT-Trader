# Robinhood Agentic read/review adapter

This package implements the accepted official MCP observation boundary. It is
not a broker implementation and must remain outside `BrokerProtocol`,
`ReadOnlyBroker`, `BROKER`, and live execution composition.

Public access is limited to account/portfolio observation, equity review, and
single-leg option review. `transport.py` owns the only private MCP session and
dispatches four literal tool names after exact schema attestation. Never add a
caller-supplied tool name, session accessor, generic `call_tool`, or mutation
tool to this package.
