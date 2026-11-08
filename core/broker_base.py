"""
Unified Broker Adapter Abstract Base Class (BRD FR-MD-001, FR-EX-001).
Standardizes broker operations across Angel One SmartAPI, MetaTrader 5 (MT5),
and simulated Paper Trading.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import pandas as pd


class BrokerAdapter(ABC):
    """Abstract Base Class for Multi-Broker Connectivity."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection and authenticate with broker."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if active connection is healthy."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully disconnect from broker."""
        pass

    @abstractmethod
    def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> Optional[float]:
        """Fetch Last Traded Price (LTP) for a symbol."""
        pass

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        timeframe: str = "M5",
        count: int = 100,
        exchange: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Fetch historical/live OHLCV candlestick dataframe.
        Returned columns: ['open', 'high', 'low', 'close', 'volume'] indexed by datetime.
        """
        pass

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,  # 'BUY' or 'SELL'
        qty: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        exchange: Optional[str] = None
    ) -> Optional[str]:
        """
        Place order with optional bracket Stop Loss and Take Profit.
        Returns unique order ID string.
        """
        pass

    @abstractmethod
    def modify_order(
        self,
        order_id: str,
        new_sl: Optional[float] = None,
        new_tp: Optional[float] = None
    ) -> bool:
        """Modify an active order's Stop Loss or Take Profit."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open/pending order."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch list of open positions."""
        pass

    @abstractmethod
    def get_available_margin(self) -> float:
        """Return available cash / net trading margin."""
        pass

