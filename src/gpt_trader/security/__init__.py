"""
Security infrastructure for GPT-Trader.

This module provides security utilities for credential management and API authentication.

Authentication
--------------
API credentials are managed through the ``SimpleAuth`` class in the brokerages module.
Credentials are loaded from environment variables or configuration files.

Environment Variables
---------------------
- ``COINBASE_CREDENTIALS_FILE``: Path to CDP JSON key file (preferred)
- ``COINBASE_CDP_API_KEY``: CDP key name (organizations/.../apiKeys/...)
- ``COINBASE_CDP_PRIVATE_KEY``: CDP private key PEM
- ``ALPACA_API_KEY_ID`` / ``ALPACA_API_SECRET_KEY``: Alpaca market-data keys
  (read-only equities daily candles; no order authority)

Security Best Practices
-----------------------
1. Never commit credentials to version control
2. Use ``.env`` files for local development (excluded via ``.gitignore``)
3. Use secure secret managers in production (AWS Secrets Manager, HashiCorp Vault)
4. Rotate API keys periodically
5. Use read-only API keys for backtesting/research

See Also
--------
- ``gpt_trader.features.brokerages.coinbase.auth``: Coinbase-specific authentication
- ``gpt_trader.app.config``: Configuration loading
"""

# Authentication is handled via SimpleAuth in the brokerages module
