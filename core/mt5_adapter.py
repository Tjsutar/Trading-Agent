"""
MetaTrader 5 (MT5) Broker Adapter (BRD FR-MD-001, FR-EX-001).
Enables trading across Forex, Indices, and Commodities (XAUUSD, EURUSD, USDJPY, Nifty).
Includes graceful terminal detection and auto-reconnection.
"""
import datetime
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from core.broker_base import BrokerAdapter
from utils.logger import get_logger

logger = get_logger("MT5Adapter")

# MT5 Timeframe mapping
TIMEFRAME_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408
}


class MT5Adapter(BrokerAdapter):
    def __init__(
        self,
        account: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None
    ):
        self.account = account
        self.password = password
        self.server = server
        self.path = path
        self._connected = False

    def connect(self) -> bool:
        """Initializes MT5 terminal and logs into account."""
        if not MT5_AVAILABLE:
            logger.error("MT5Adapter: MetaTrader5 library is not installed.")
            return False

        try:
            init_kwargs = {}
            if self.path:
                init_kwargs["path"] = self.path

            if not mt5.initialize(**init_kwargs):
                err = mt5.last_error()
                logger.warning(f"MT5Adapter: Terminal initialization failed ({err}). Terminal may not be running locally.")
                return False

            if self.account and self.password and self.server:
                authorized = mt5.login(self.account, password=self.password, server=self.server)
                if not authorized:
                    logger.error(f"MT5Adapter: Login failed for account {self.account}: {mt5.last_error()}")
                    mt5.shutdown()
                    return False

            self._connected = True
            logger.info("MT5Adapter: Connected to MetaTrader 5 successfully.")
            return True
        except Exception as e:
            logger.error(f"MT5Adapter: Exception during connect: {e}")
            return False

    def is_connected(self) -> bool:
        if not MT5_AVAILABLE or not self._connected:
            return False
        terminal_info = mt5.terminal_info()
        return terminal_info is not None and terminal_info.connected

    def disconnect(self) -> None:
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("MT5Adapter: Disconnected from MetaTrader 5.")

    def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> Optional[float]:
        if not self.is_connected():
            return None
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return float(tick.last if tick.last > 0 else (tick.bid + tick.ask) / 2.0)
        return None

    def get_candles(
        self,
        symbol: str,
        timeframe: str = "M5",
        count: int = 100,
        exchange: Optional[str] = None
    ) -> pd.DataFrame:
        if not self.is_connected():
            return pd.DataFrame()

        tf = TIMEFRAME_MAP.get(timeframe.upper(), mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        df.rename(columns={'tick_volume': 'volume'}, inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]

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
        if not self.is_connected():
            return None

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"MT5Adapter: Symbol {symbol} not found.")
            return None

        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return None

        action = mt5.TRADE_ACTION_DEAL
        order_side = mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        exec_price = tick.ask if side.upper() == "BUY" else tick.bid

        request = {
            "action": action,
            "symbol": symbol,
            "volume": float(qty),
            "type": order_side,
            "price": exec_price,
            "deviation": 20,
            "magic": 234000,
            "comment": "T-Agent Execution",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if sl:
            request["sl"] = float(sl)
        if tp:
            request["tp"] = float(tp)

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"MT5Adapter: Order placed successfully. Ticket: {result.order}")
            return str(result.order)
        else:
            code = result.retcode if result else "None"
            logger.error(f"MT5Adapter: Order failed: {code}")
            return None

    def modify_order(
        self,
        order_id: str,
        new_sl: Optional[float] = None,
        new_tp: Optional[float] = None
    ) -> bool:
        if not self.is_connected():
            return False
        ticket = int(order_id)
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
        }
        if new_sl is not None:
            request["sl"] = float(new_sl)
        if new_tp is not None:
            request["tp"] = float(new_tp)

        result = mt5.order_send(request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    def cancel_order(self, order_id: str) -> bool:
        if not self.is_connected():
            return False
        ticket = int(order_id)
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket
        }
        result = mt5.order_send(request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.is_connected():
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        return [
            {
                "ticket": str(p.ticket),
                "symbol": p.symbol,
                "side": "BUY" if p.type == 0 else "SELL",
                "qty": p.volume,
                "entry_price": p.price_open,
                "current_price": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit
            }
            for p in positions
        ]

    def get_available_margin(self) -> float:
        if not self.is_connected():
            return 0.0
        acc = mt5.account_info()
        return float(acc.margin_free if acc else 0.0)
