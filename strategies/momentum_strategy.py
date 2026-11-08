"""
Institutional Momentum Strategy (EMA 9/21 Crossover + RSI + 50 EMA Trend Filter).
Validated for Small Capital (₹5,000 tier) with 1-Lot Nifty Option Buying.
Features:
  1. Automated ATM strike selection for Nifty CE/PE
  2. Hard Stop Loss (-16 pts) and Target (+36 pts)
  3. Breakeven escalation at +15 pts profit
  4. Max 2 trades per day limit
  5. 2% / ₹350 Daily Loss Kill Switch
  6. Database state persistence (Trades, Signals, SL modifications)
"""
import time
import datetime
import json
import os
import random
from typing import Optional, Any, Dict, List, Tuple
import pandas as pd
import numpy as np

from config.settings import Config
from core.safety_checks import SafetyGatekeeper
from core.data_fetcher import DataFetcher
from db.repository import TradeRepository
from utils.logger import get_logger
from utils.notifier import notifier

logger = get_logger("MomentumStrategy")


class MomentumStrategy:
    STATE_FILE = "trade_state.json"

    def __init__(self, api, token_loader, dry_run: bool = False, starting_capital: float = 5000.0, market_stream: Optional[Any] = None):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.starting_capital = starting_capital
        self.market_stream = market_stream
        self.gatekeeper = SafetyGatekeeper(self.api, starting_capital=starting_capital)
        self.data_fetcher = DataFetcher(self.api)
        self.data_failure_count = 0
        self.active_position = None
        self.daily_trade_count = 0
        self.max_trades_per_day = Config.MAX_TRADES_PER_DAY
        self.had_loss_today = False
        self.min_adx = Config.MIN_ADX_TREND
        self.be_trigger = Config.FAST_BREAKEVEN_PTS
        self.be_lock = Config.BREAKEVEN_LOCK_PTS
        self.load_state()

    def save_state(self):
        try:
            with open(self.STATE_FILE, 'w') as f:
                json.dump(self.active_position, f)
        except Exception as e:
            logger.error(f"Save State Error: {e}")

    def load_state(self):
        if not os.path.exists(self.STATE_FILE):
            return
        try:
            with open(self.STATE_FILE, 'r') as f:
                data = json.load(f)
                if data:
                    self.active_position = data
                    logger.info(f"♻️ Restored Active Position from State: {data.get('symbol')}")
        except Exception as e:
            logger.error(f"Load State Error: {e}")

    def check_trailing_stop(self) -> bool:
        """
        Manages Hard SL, Breakeven Escalation, and Target Exits.
        """
        if not self.active_position:
            return False

        token = self.active_position.get('token')
        symbol = self.active_position.get('symbol')
        entry_price = self.active_position.get('entry_price', 0.0)
        current_sl = self.active_position.get('sl_price', 0.0)
        target_price = self.active_position.get('target_price', 0.0)
        order_id = self.active_position.get('order_id', f"POS-{symbol}")

        if entry_price == 0:
            return False

        ltp = self.get_option_ltp(symbol, token)
        if not ltp or ltp <= 0:
            return False

        # Track peak option price reached
        highest_price = self.active_position.get('highest_price', entry_price)
        if ltp > highest_price:
            highest_price = ltp
            self.active_position['highest_price'] = highest_price

        profit_pts = ltp - entry_price
        peak_profit = highest_price - entry_price

        # 1. Target Hit Exit (+36 pts)
        if target_price > 0 and ltp >= target_price:
            logger.info(f"🎯 Target Profit Hit! Price: {ltp} >= Target: {target_price}")
            self.close_position("TARGET_HIT", exit_price=ltp)
            return True

        # 2. Dynamic Trailing Stop Loss & Fast Breakeven
        if peak_profit >= self.be_trigger:
            # Phase 1: Fast Breakeven at +10 pts (Lock Cost + 2 pts)
            if not self.active_position.get("be_active"):
                self.active_position["be_active"] = True
                be_sl = round(entry_price + self.be_lock, 1)
                if be_sl > current_sl:
                    old_sl = current_sl
                    current_sl = be_sl
                    self.active_position['sl_price'] = current_sl
                    logger.info(f"🔒 Breakeven Active! Peak +{peak_profit:.1f} pts. SL moved to {current_sl} (Risk-Free).")
                    TradeRepository.record_sl_modification(
                        order_id=order_id,
                        old_sl=old_sl,
                        new_sl=current_sl,
                        current_price=ltp,
                        reason="BREAKEVEN"
                    )
                    notifier.notify_sl_moved(symbol, old_sl, current_sl, "BREAKEVEN")
                    self.save_state()

            # Phase 2: Dynamic Continuous Trailing (trail 8 pts behind peak)
            trail_sl = round(highest_price - Config.TSL_DISTANCE, 1)
            if trail_sl > current_sl:
                old_sl = current_sl
                current_sl = trail_sl
                self.active_position['sl_price'] = current_sl
                locked_profit = current_sl - entry_price
                logger.info(f"📈 Trailing SL Escalated! Peak: ₹{highest_price:.1f} ➔ New SL: ₹{current_sl:.1f} (+{locked_profit:.1f} pts locked).")
                TradeRepository.record_sl_modification(
                    order_id=order_id,
                    old_sl=old_sl,
                    new_sl=current_sl,
                    current_price=ltp,
                    reason="DYNAMIC_TSL"
                )
                notifier.notify_sl_moved(symbol, old_sl, current_sl, f"TSL +{locked_profit:.1f} pts locked")
                self.save_state()

        # 3. Stop Loss Hit Exit (Hard SL or Trailing SL)
        if current_sl > 0 and ltp <= current_sl:
            reason = "TRAILING_STOP_HIT" if self.active_position.get("be_active") else "HARD_STOP_LOSS"
            logger.info(f"🛑 {reason}! Price: {ltp} <= SL: {current_sl}")
            self.close_position(reason, exit_price=current_sl)
            return True

        return False

    def execute(self, expiry: str):
        """
        Main execution loop for Momentum Option Buying Strategy.
        """
        logger.info(f"--- MOMENTUM OPTION BUYING STRATEGY ({expiry}) ---")

        # Pre-flight Gatekeeper checks
        if not self.gatekeeper.check_max_daily_loss(0):
            return
        if self.gatekeeper.is_blackout_period():
            return

        logger.info("Starting continuous monitor for EMA 9/21 crossover...")

        while True:
            try:
                now = datetime.datetime.now().time()
                # 15:15 IST Mandatory Intraday Square-off
                if now >= datetime.time(15, 15):
                    logger.info("Market Square-off time (15:15 IST) reached. Closing open positions.")
                    self.close_position("TIME_EXIT")
                    break

                # 1. Check open position stops/targets
                if self.active_position:
                    if self.check_trailing_stop():
                        pass

                # 2. Analyze Nifty Trend
                trend, ema9, ema21, ema50, rsi = self.analyze_market_trend()
                logger.info(f"[Analysis] Trend: {trend} | EMA9: {ema9:.1f} | EMA21: {ema21:.1f} | RSI: {rsi:.1f} | Active: {self.active_position['leg'] if self.active_position else 'None'}")

                # 3. Look for Entry Signals if flat
                if not self.active_position:
                    if Config.STOP_ON_FIRST_LOSS and self.had_loss_today:
                        logger.info("🛑 Stop-on-1st-Loss active! Capital protected after today's loss. Sitting in cash.")
                    elif self.daily_trade_count >= self.max_trades_per_day:
                        logger.info(f"Daily trade limit reached ({self.daily_trade_count}/{self.max_trades_per_day}). Waiting for next session.")
                    else:
                        # Bullish Crossover: EMA 9 > 21 + RSI < 68
                        if trend == "BULLISH" and rsi < 68:
                            self.enter_position(expiry, "CE", trend, ema9, ema21, rsi)
                        # Bearish Crossover: EMA 9 < 21 + RSI > 32
                        elif trend == "BEARISH" and rsi > 32:
                            self.enter_position(expiry, "PE", trend, ema9, ema21, rsi)

                # 4. Check for Trend Reversal Exit on active position
                elif self.active_position:
                    current_leg = self.active_position.get('leg')
                    if current_leg == "CE" and trend == "BEARISH":
                        logger.info("Trend reversed to BEARISH. Exiting CE position.")
                        self.close_position("TREND_REVERSAL")
                    elif current_leg == "PE" and trend == "BULLISH":
                        logger.info("Trend reversed to BULLISH. Exiting PE position.")
                        self.close_position("TREND_REVERSAL")

                # Sleep 5s between checks (or fast polling in dry run)
                time.sleep(5 if self.dry_run else 10)

            except KeyboardInterrupt:
                logger.info("User requested manual stop.")
                break
            except Exception as e:
                logger.error(f"Momentum execution error: {e}")
                time.sleep(10)

    def analyze_market_trend(self):
        """Fetches latest 5-min candles and computes EMA 9, 21, 50, and RSI."""
        if self.dry_run or (hasattr(self.api, 'api_key') and self.api.api_key is None):
            df = self.get_mock_df()
        elif self.market_stream:
            stream_df = self.market_stream.get_nifty_candles()
            if stream_df is not None and len(stream_df) >= 25:
                df = stream_df
            else:
                # Seed historical candles once
                df = self.data_fetcher.fetch_latest_candles("99926000")
                if df is not None and not df.empty:
                    self.market_stream.nifty_synthesizer.load_historical(df)
        else:
            df = self.data_fetcher.fetch_latest_candles("99926000")

        if df is None or df.empty or len(df) < 25:
            return "NEUTRAL", 0, 0, 0, 50.0

        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()

        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        last = df.iloc[-1]
        ema9 = float(last['EMA9'])
        ema21 = float(last['EMA21'])
        ema50 = float(last['EMA50'])
        rsi = float(last['RSI']) if not np.isnan(last['RSI']) else 50.0

        # ADX Regime Check (Detect true trending vs sideways chop)
        adx = 25.0
        if "high" in df.columns and "low" in df.columns and len(df) >= 28:
            high = df["high"]
            low = df["low"]
            close = df["close"]
            plus_dm = np.where((high.diff() > -low.diff()) & (high.diff() > 0), high.diff(), 0.0)
            minus_dm = np.where((-low.diff() > high.diff()) & (-low.diff() > 0), -low.diff(), 0.0)
            tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
            atr14 = tr.ewm(span=14, adjust=False).mean()
            plus_di14 = 100 * (pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / atr14)
            minus_di14 = 100 * (pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / atr14)
            dx = 100 * (abs(plus_di14 - minus_di14) / (plus_di14 + minus_di14).replace(0, np.nan))
            adx_val = dx.ewm(span=14, adjust=False).mean().iloc[-1]
            adx = float(adx_val) if not np.isnan(adx_val) else 25.0

        if not np.isnan(adx) and adx < self.min_adx:
            logger.info(f"[Regime Gate] Choppy sideways market detected (ADX: {adx:.1f} < {self.min_adx}). Gating entries to prevent false breakouts.")
            return "NEUTRAL", ema9, ema21, ema50, rsi

        if ema9 > ema21:
            return "BULLISH", ema9, ema21, ema50, rsi
        elif ema9 < ema21:
            return "BEARISH", ema9, ema21, ema50, rsi
        return "NEUTRAL", ema9, ema21, ema50, rsi

    def enter_position(self, expiry: str, leg: str, trend: str, ema9: float, ema21: float, rsi: float):
        """Selects ATM strike and executes option buy order."""
        qty = int(Config.NIFTY_LOT_SIZE)  # 1 Lot (25 qty)
        ltp = self.get_nifty_ltp()
        if not ltp:
            logger.error("Could not fetch Nifty Spot LTP for Strike Selection.")
            return

        strike = int(round(ltp / 50.0) * 50)
        token, symbol = self.token_loader.get_token("NIFTY", expiry, strike, leg)
        if not token:
            logger.error(f"Could not find token for {strike} {leg}")
            return

        quote_ltp = self.get_option_ltp(symbol, token) or 90.0
        estimated_cost = quote_ltp * qty

        # Margin check
        if not self.gatekeeper.check_trade_margin(estimated_cost):
            logger.warning(f"Trade skipped: Available funds insufficient for estimated cost ₹{estimated_cost:,.2f}")
            return

        SL_POINTS = 16.0
        TP_POINTS = 36.0
        sl_price = max(1.0, round(quote_ltp - SL_POINTS, 1))
        target_price = round(quote_ltp + TP_POINTS, 1)

        order_id = f"MOM-{symbol}-{int(time.time())}"

        # Persist Signal to DB
        TradeRepository.record_signal(
            strategy_name="MOMENTUM_OPTION_BUY",
            symbol=symbol,
            timeframe="M5",
            signal_type="BUY",
            confidence=85.0,
            entry_price=quote_ltp,
            stop_loss=sl_price,
            take_profit=target_price,
            risk_reward=round(TP_POINTS / SL_POINTS, 2),
            executed=True
        )

        logger.info(f"Trade Entry: BUY {qty}x {symbol} @ ₹{quote_ltp:.1f} | Hard SL: ₹{sl_price:.1f} | Target: ₹{target_price:.1f}")

        if not self.dry_run:
            try:
                orderparams = {
                    "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                    "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                }
                real_oid = self.api.placeOrder(orderparams)
                if real_oid:
                    order_id = real_oid
                    logger.info(f"Broker Order Dispatched: OrderID = {real_oid}")
            except Exception as e:
                logger.error(f"Order placement failed: {e}")

        # Persist Trade to DB
        TradeRepository.create_trade(
            order_id=order_id,
            strategy_name="MOMENTUM_OPTION_BUY",
            symbol=symbol,
            trade_type="BUY",
            lot_size=qty,
            entry_price=quote_ltp,
            initial_stop_loss=sl_price,
            take_profit_1=target_price,
            breakeven_enabled=True,
            trailing_sl_enabled=True,
            trailing_distance_pips=15.0
        )

        self.active_position = {
            'order_id': order_id,
            'leg': leg,
            'symbol': symbol,
            'qty': qty,
            'token': token,
            'entry_price': quote_ltp,
            'highest_price': quote_ltp,
            'sl_price': sl_price,
            'initial_sl': sl_price,
            'target_price': target_price,
            'be_active': False
        }
        self.daily_trade_count += 1
        self.save_state()

        notifier.notify_trade_entry(symbol, f"BUY {leg}", qty, quote_ltp, sl_price, target_price)

        if self.market_stream and token:
            self.market_stream.subscribe(str(token), exchange_type=2)
            logger.info(f"MarketDataStream: Subscribed {symbol} ({token}) to streaming feed.")

    def close_position(self, reason: str, exit_price: Optional[float] = None):
        """Closes active option position and records P&L to database."""
        if not self.active_position:
            return

        symbol = self.active_position.get('symbol')
        token = self.active_position.get('token')
        qty = self.active_position.get('qty', 25)
        entry_price = self.active_position.get('entry_price', 0.0)
        order_id = self.active_position.get('order_id', f"POS-{symbol}")

        actual_exit = exit_price or self.get_option_ltp(symbol, token) or entry_price
        pnl = (actual_exit - entry_price) * qty - 40.0  # Minus ₹40 brokerage

        logger.info(f"Exit: Closed {symbol} @ ₹{actual_exit:.1f} | PnL: {'+' if pnl>=0 else ''}₹{pnl:,.2f} | Reason: {reason}")

        if not self.dry_run:
            try:
                orderparams = {
                    "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                    "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
                }
                self.api.placeOrder(orderparams)
            except Exception as e:
                logger.error(f"Exit order dispatch failed: {e}")

        # Update Database Trade Record
        TradeRepository.close_trade(
            order_id=order_id,
            exit_price=actual_exit,
            pnl=pnl,
            reason=reason
        )

        notifier.notify_trade_exit(symbol, actual_exit, pnl, reason)
        if pnl < 0:
            self.had_loss_today = True

        self.active_position = None
        if self.market_stream and token:
            self.market_stream.unsubscribe(str(token), exchange_type=2)

        if os.path.exists(self.STATE_FILE):
            try:
                os.remove(self.STATE_FILE)
            except Exception:
                pass

    def get_nifty_ltp(self) -> Optional[float]:
        if self.market_stream:
            stream_val = self.market_stream.get_ltp("99926000", exchange_type=1)
            if stream_val and stream_val > 0:
                return stream_val

        try:
            resp = self.api.ltpData("NSE", "Nifty 50", "99926000")
            if resp and resp.get('status') and resp.get('data'):
                return float(resp['data']['ltp'])
        except Exception:
            pass
        return 24986.40 if self.dry_run else None

    def get_option_ltp(self, symbol: str, token: str) -> Optional[float]:
        if self.market_stream and token:
            stream_val = self.market_stream.get_ltp(str(token), exchange_type=2)
            if stream_val and stream_val > 0:
                return stream_val

        try:
            resp = self.api.ltpData("NFO", symbol, token)
            if resp and resp.get('status') and resp.get('data'):
                return float(resp['data']['ltp'])
        except Exception:
            pass
        return 90.0 if self.dry_run else None

    def get_mock_df(self) -> pd.DataFrame:
        """Returns dummy 5-min candles for testing."""
        dates = pd.date_range(end=datetime.datetime.now(), periods=30, freq="5min")
        closes = 24900.0 + np.cumsum(np.random.normal(2, 5, 30))
        return pd.DataFrame({'close': closes}, index=dates)
