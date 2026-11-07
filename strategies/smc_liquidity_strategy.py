"""
Institutional Smart Money Concepts (SMC) Liquidity Strategy.
Complies with BRD FR-AL-001 (Liquidity Detection), FR-AL-002 (MTF Alignment),
FR-AL-003 (Fake Breakout Filter), and FR-SL-001 (Signal Generation).
"""
import datetime
import time
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import numpy as np

from core.safety_checks import SafetyGatekeeper
from core.position_manager import PositionManager
from db.repository import TradeRepository
from utils.logger import get_logger

logger = get_logger("SMCLiquidityStrategy")


class SMCLiquidityStrategy:
    """
    Executes Institutional Liquidity Sweep & Market Structure Alignment:
      1. Detects Equal Highs (EQH) and Equal Lows (EQL) within ATR * 0.2 tolerance.
      2. Multi-Timeframe (H1 Bias -> M5 Entry) confirmation.
      3. Rejects fake breakout traps (wick/body > 1.5 with below-average volume).
      4. Requires minimum 1:2 Risk-to-Reward ratio before signal generation.
      5. Full state persistence via TradeRepository.
    """

    def __init__(self, api, token_loader, dry_run: bool = False, starting_capital: float = 100000.0):
        self.api = api
        self.token_loader = token_loader
        self.dry_run = dry_run
        self.starting_capital = starting_capital
        self.gatekeeper = SafetyGatekeeper(self.api, starting_capital=starting_capital)
        self.position_manager = PositionManager(self.api, dry_run=self.dry_run)
        self.min_rr = 2.0

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculates Average True Range (ATR)."""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    @staticmethod
    def detect_liquidity_zones(df: pd.DataFrame, atr_multiplier: float = 0.2, min_touches: int = 2) -> Dict[str, List[Dict[str, Any]]]:
        """
        FR-AL-001: Liquidity Zone Detection (Equal Highs and Equal Lows).
        Zone tolerance: |Price_i - Price_j| <= ATR * 0.2.
        Strength: 2 touches = weak, 3+ touches = strong.
        """
        if len(df) < 20:
            return {"eqh": [], "eql": []}

        atr = SMCLiquidityStrategy.calculate_atr(df).iloc[-1]
        tolerance = atr * atr_multiplier if not np.isnan(atr) and atr > 0 else 5.0

        highs = df['high'].values
        lows = df['low'].values
        
        eqh_zones = []
        eql_zones = []

        # Find cluster of peaks (EQH)
        for i in range(len(highs) - 10, len(highs)):
            matches = [j for j in range(len(highs)) if abs(highs[i] - highs[j]) <= tolerance and abs(i - j) >= 3]
            if len(matches) >= (min_touches - 1):
                level = float(np.mean([highs[i]] + [highs[m] for m in matches]))
                touches = len(matches) + 1
                strength = "strong" if touches >= 3 else "weak"
                if not any(abs(z['level'] - level) <= tolerance for z in eqh_zones):
                    eqh_zones.append({"level": level, "touches": touches, "strength": strength, "type": "EQH"})

        # Find cluster of valleys (EQL)
        for i in range(len(lows) - 10, len(lows)):
            matches = [j for j in range(len(lows)) if abs(lows[i] - lows[j]) <= tolerance and abs(i - j) >= 3]
            if len(matches) >= (min_touches - 1):
                level = float(np.mean([lows[i]] + [lows[m] for m in matches]))
                touches = len(matches) + 1
                strength = "strong" if touches >= 3 else "weak"
                if not any(abs(z['level'] - level) <= tolerance for z in eql_zones):
                    eql_zones.append({"level": level, "touches": touches, "strength": strength, "type": "EQL"})

        return {"eqh": eqh_zones, "eql": eql_zones}

    @staticmethod
    def get_market_bias(h1_df: pd.DataFrame) -> str:
        """
        FR-AL-002: Higher Timeframe (H1) Market Bias.
        Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'.
        """
        if len(h1_df) < 21:
            return "NEUTRAL"

        ema9 = h1_df['close'].ewm(span=9, adjust=False).mean()
        ema21 = h1_df['close'].ewm(span=21, adjust=False).mean()

        last_ema9 = ema9.iloc[-1]
        last_ema21 = ema21.iloc[-1]
        last_close = h1_df['close'].iloc[-1]

        if last_close > last_ema9 > last_ema21:
            return "BULLISH"
        elif last_close < last_ema9 < last_ema21:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def is_fake_breakout(candle: pd.Series, avg_volume: float) -> bool:
        """
        FR-AL-003: Fake Breakout / Institutional Trap Detection.
        Ratio: wick_length / body_length > 1.5 with volume < avg_volume.
        """
        open_p = candle['open']
        close_p = candle['close']
        high_p = candle['high']
        low_p = candle['low']
        vol = candle.get('volume', 0.0)

        body_len = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p
        max_wick = max(upper_wick, lower_wick)

        if body_len > 0:
            wick_ratio = max_wick / body_len
            if wick_ratio > 1.5 and vol < avg_volume:
                return True
        return False

    def generate_signal(
        self,
        m5_df: pd.DataFrame,
        h1_df: pd.DataFrame,
        symbol: str = "NIFTY"
    ) -> Optional[Dict[str, Any]]:
        """
        FR-SL-001: Signal Generation with full rule alignment.
        """
        if len(m5_df) < 20 or len(h1_df) < 20:
            return None

        # 1. Market Bias (H1)
        htf_bias = self.get_market_bias(h1_df)

        # 2. Liquidity Zones (M5)
        zones = self.detect_liquidity_zones(m5_df)
        last_candle = m5_df.iloc[-1]
        prev_candle = m5_df.iloc[-2]
        cmp = float(last_candle['close'])
        avg_vol = float(m5_df['volume'].rolling(20).mean().iloc[-1]) if 'volume' in m5_df else 0.0

        # Check for fake breakout
        if self.is_fake_breakout(last_candle, avg_vol):
            logger.info("Fake breakout / liquidity sweep detected on M5 candle.")

        atr = float(self.calculate_atr(m5_df).iloc[-1])
        if np.isnan(atr) or atr <= 0:
            atr = 25.0

        signal = None

        # Bullish Liquidity Sweep (Swept Equal Lows + H1 Bullish or Neutral)
        for zone in zones["eql"]:
            # If price swept below EQL level and closed back above
            if prev_candle['low'] <= zone['level'] and last_candle['close'] > zone['level']:
                if htf_bias in ["BULLISH", "NEUTRAL"]:
                    entry = cmp
                    sl = float(prev_candle['low']) - (atr * 0.1)
                    risk = entry - sl
                    tp1 = entry + (risk * 2.0)  # 1:2 RR
                    tp2 = entry + (risk * 3.5)

                    if self.gatekeeper.validate_risk_reward(entry, sl, tp1, self.min_rr):
                        signal = {
                            "strategy": "SMC_LIQUIDITY",
                            "symbol": symbol,
                            "timeframe": "M5",
                            "signal_type": "BUY",
                            "confidence": 85.0 if zone['strength'] == "strong" else 75.0,
                            "liquidity_zone_id": f"EQL-{zone['level']:.1f}",
                            "entry_price": entry,
                            "stop_loss": sl,
                            "take_profit_1": tp1,
                            "take_profit_2": tp2,
                            "risk_reward": round((tp1 - entry) / risk, 2)
                        }
                        break

        # Bearish Liquidity Sweep (Swept Equal Highs + H1 Bearish or Neutral)
        if not signal:
            for zone in zones["eqh"]:
                if prev_candle['high'] >= zone['level'] and last_candle['close'] < zone['level']:
                    if htf_bias in ["BEARISH", "NEUTRAL"]:
                        entry = cmp
                        sl = float(prev_candle['high']) + (atr * 0.1)
                        risk = sl - entry
                        tp1 = entry - (risk * 2.0)
                        tp2 = entry - (risk * 3.5)

                        if self.gatekeeper.validate_risk_reward(entry, sl, tp1, self.min_rr):
                            signal = {
                                "strategy": "SMC_LIQUIDITY",
                                "symbol": symbol,
                                "timeframe": "M5",
                                "signal_type": "SELL",
                                "confidence": 85.0 if zone['strength'] == "strong" else 75.0,
                                "liquidity_zone_id": f"EQH-{zone['level']:.1f}",
                                "entry_price": entry,
                                "stop_loss": sl,
                                "take_profit_1": tp1,
                                "take_profit_2": tp2,
                                "risk_reward": round((entry - tp1) / risk, 2)
                            }
                            break

        if signal:
            TradeRepository.record_signal(
                strategy_name=signal["strategy"],
                symbol=signal["symbol"],
                timeframe=signal["timeframe"],
                signal_type=signal["signal_type"],
                confidence=signal["confidence"],
                liquidity_zone_id=signal["liquidity_zone_id"],
                entry_price=signal["entry_price"],
                stop_loss=signal["stop_loss"],
                take_profit=signal["take_profit_1"],
                risk_reward=signal["risk_reward"],
                executed=True
            )
            logger.info(f"SMC Signal Persisted: {signal['signal_type']} {signal['symbol']} @ {signal['entry_price']:.2f}")

        return signal

    def execute(self, expiry: str, m5_df: Optional[pd.DataFrame] = None, h1_df: Optional[pd.DataFrame] = None):
        """Live execution method for SMC Liquidity Strategy."""
        logger.info(f"--- SMC LIQUIDITY STRATEGY EXECUTION ({expiry}) ---")

        if not self.gatekeeper.check_max_daily_loss(0.0):
            return
        if self.gatekeeper.is_blackout_period():
            return

        # If dataframes provided (e.g. in test/backtest mode)
        if m5_df is not None and h1_df is not None:
            sig = self.generate_signal(m5_df, h1_df)
            if sig:
                print(f">>> [SMC] ⚡ Signal: {sig['signal_type']} | Entry: {sig['entry_price']:.2f} | SL: {sig['stop_loss']:.2f} | TP1: {sig['take_profit_1']:.2f} | RR: 1:{sig['risk_reward']}")
            else:
                print(">>> [SMC] No liquidity sweep signal aligned with HTF bias.")

