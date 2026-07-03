"""Historical data management for backtesting."""

from .manager import HistoricalDataManager, create_coinbase_data_provider

__all__ = [
    "HistoricalDataManager",
    "create_coinbase_data_provider",
]
