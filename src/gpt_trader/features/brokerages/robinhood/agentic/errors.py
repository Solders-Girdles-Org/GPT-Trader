"""Fail-closed errors for Robinhood Agentic read/review access."""


class RobinhoodAgenticViolation(ValueError):
    """Raised when MCP metadata or provider evidence violates the accepted contract."""


class RobinhoodAgenticUnavailable(RuntimeError):
    """Raised when the optional MCP transport cannot be initialized safely."""


__all__ = ["RobinhoodAgenticUnavailable", "RobinhoodAgenticViolation"]
