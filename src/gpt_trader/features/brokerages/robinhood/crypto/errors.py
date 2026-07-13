"""Robinhood Crypto boundary and transport errors."""


class RobinhoodCryptoClientViolation(ValueError):
    """Raised before or after dispatch when the approved API boundary is violated."""


class RobinhoodCryptoTransportError(RuntimeError):
    """Raised for a provider or transport failure without echoing secrets or identity."""


__all__ = ["RobinhoodCryptoClientViolation", "RobinhoodCryptoTransportError"]
