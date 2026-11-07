"""
Institutional Paper Broker Adapter.
Provides realistic simulated execution, bracket orders, slippage, and position tracking
for offline testing and demo account validation (BRD Section 10.2).
"""
import uuid
import datetime
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np

from core.broker_base import BrokerAdapter
from utils.logger import get_logger

logger = get_logger("PaperBroker")


class PaperBroker(BrokerAdapter):
    def __init__(self, starting_cash: float = 100000.0, default_prices: Optional[Dict[str, float]] = None):
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.prices = default_prices or {
            "NIFTY": 23000.0,
            "BANKNIFTY": 49000.0,
            "XAUUSD": 2700.0,
            "EURUSD": 1.0850,
            "USDJPY": 154.50
        }
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self._connected = True

    def connect(self) -> bool:
        self._connected = True
        logger.info("PaperBroker: Connected successfully to simulated environment.")
        return True

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False
        logger.info("PaperBroker: Disconnected.")

    def set_ltp(self, symbol: str, price: float):
        """Allows injecting specific price levels during tests/simulation."""
        self.prices[symbol] = price

    def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> Optional[float]:
        # Return stored price with slight organic micro-fluctuation (+/- 0.05%)
        base_price = self.prices.get(symbol, 100.0)
        noise = base_price * np.random.uniform(-0.0005, 0.0005)
        return round(base_price + noise, 2)

    def get_candles(
        self,
        symbol: str,
        timeframe: str = "M5",
        count: int = 100,
        exchange: Optional[str] = None
    ) -> pd.DataFrame:
        """Generates realistic synthetic OHLCV candles."""
        base_price = self.prices.get(symbol, 23000.0)
        dates = pd.date_range(end=datetime.datetime.now(), periods=count, freq="5min")
        
        # Random walk
        returns = np.random.normal(0, 0.002, count)
        price_paths = base_price * np.exp(np.cumsum(returns))
        
        highs = price_paths * (1 + np.abs(np.random.normal(0, 0.001, count)))
        lows = price_paths * (1 - np.abs(np.random.normal(0, 0.001, count)))
        opens = np.roll(price_paths, 1)
        opens[0] = base_price
        volumes = np.random.randint(500, 5000, count)

        df = pd.DataFrame({
            "open": opens,
            "high": np.maximum(highs, np.maximum(opens, price_paths)),
            "low": np.minimum(lows, np.minimum(opens, price_paths)),
            "close": price_paths,
            "volume": volumes
        }, index=dates)

        return df

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        exchange: Optional[str] = None
    ) -> Optional[str]:
        order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        fill_price = price if (order_type == "LIMIT" and price) else self.get_ltp(symbol)

        cost = fill_price * qty
        if cost > self.cash:
            logger.warning(f"PaperBroker: Order rejected! Cost ₹{cost:,.2f} exceeds cash ₹{self.cash:,.2f}")
            return None

        self.cash -= cost

        order_record = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side.upper(),
            "qty": qty,
            "price": fill_price,
            "sl": sl,
            "tp": tp,
            "status": "FILLED",
            "timestamp": datetime.datetime.now()
        }
        self.orders[order_id] = order_record

        # Update position
        if symbol in self.positions:
            pos = self.positions[symbol]
            if pos["side"] == side.upper():
                total_qty = pos["qty"] + qty
                avg_price = ((pos["entry_price"] * pos["qty"]) + (fill_price * qty)) / total_qty
                pos["qty"] = total_qty
                pos["entry_price"] = avg_price
            else:
                # Partial or full close
                if qty >= pos["qty"]:
                    del self.positions[symbol]
                else:
                    pos["qty"] -= qty
        else:
            self.positions[symbol] = {
                "symbol": symbol,
                "side": side.upper(),
                "qty": qty,
                "entry_price": fill_price,
                "sl": sl,
                "tp": tp,
                "order_id": order_id
            }

        logger.info(f"PaperBroker: Filled {side.upper()} {qty}x {symbol} @ ₹{fill_price:,.2f} [ID: {order_id}]")
        return order_id

    def modify_order(
        self,
        order_id: str,
        new_sl: Optional[float] = None,
        new_tp: Optional[float] = None
    ) -> bool:
        if order_id in self.orders:
            if new_sl is not None:
                self.orders[order_id]["sl"] = new_sl
            if new_tp is not None:
                self.orders[order_id]["tp"] = new_tp
            logger.info(f"PaperBroker: Modified Order {order_id} -> SL: {new_sl}, TP: {new_tp}")
            return True
        return False

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id]["status"] = "CANCELLED"
            logger.info(f"PaperBroker: Cancelled Order {order_id}")
            return True
        return False

    def get_positions(self) -> List[Dict[str, Any]]:
        return list(self.positions.values())

    def get_available_margin(self) -> float:
        return self.cash
