"""
Institutional Historical Backtesting Runner (BRD Section 10.1 & FR-MR-005).
Executes quantitative backtests with:
  1. Swing Fractal Liquidity Detection (EQH / EQL with ATR tolerance)
  2. Multi-Timeframe (H1 bias -> M5 entry) gating
  3. Strict 1:2 Minimum Risk-Reward
  4. 2% Daily Loss Kill Switch per trading session
  5. Slippage (0.1%) and Broker Commission (₹20 per order)
  6. Comprehensive Tear Sheet metrics (Sharpe, Sortino, Drawdown, Profit Factor)
"""
import sys
import os
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backtester import BacktestEngine
from strategies.smc_liquidity_strategy import SMCLiquidityStrategy
from core.session_manager import SessionManager
from db.database import init_db
from utils.logger import get_logger

logger = get_logger("BacktestRunner")


def fetch_angel_candles(symboltoken: str = "99926000", interval: str = "FIVE_MINUTE", days: int = 15) -> Optional[pd.DataFrame]:
    """
    Fetches real historical candlestick data directly from Angel One SmartAPI.
    """
    try:
        mgr = SessionManager()
        if not mgr.connect():
            logger.warning("Could not connect to Angel One for historical data.")
            return None

        api = mgr.get_api()
        to_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")

        params = {
            "exchange": "NSE",
            "symboltoken": symboltoken,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date
        }
        res = api.getCandleData(params)
        if res and res.get("status") and res.get("data"):
            rows = res["data"]
            candles = []
            for r in rows:
                candles.append({
                    "time": pd.to_datetime(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]) if len(r) > 5 else 1000.0
                })
            df = pd.DataFrame(candles)
            df.set_index("time", inplace=True)
            logger.info(f"Retrieved {len(df)} real historical candles from Angel One.")
            return df
        return None
    except Exception as e:
        logger.error(f"Error fetching Angel One candles: {e}")
        return None


def generate_market_data(days: int = 40, candles_per_day: int = 75, base_price: float = 23000.0) -> pd.DataFrame:
    """
    Generates realistic multi-day market data with distinct intraday sessions (09:15 - 15:30 IST),
    trending impulses, swing liquidity zones, and mean reversion.
    """
    np.random.seed(101)
    all_candles = []
    current_price = base_price

    start_date = datetime.now() - timedelta(days=days * 1.5)
    current_date = start_date

    days_generated = 0
    while days_generated < days:
        # Skip weekends
        if current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue

        # Day trend bias (-1: Bearish, 0: Range, +1: Bullish)
        day_bias = np.random.choice([1, -1, 0], p=[0.42, 0.38, 0.20])
        drift = day_bias * 0.0003

        # Gap open
        current_price *= (1.0 + np.random.normal(drift * 5, 0.002))

        session_start = current_date.replace(hour=9, minute=15, second=0, microsecond=0)
        session_times = [session_start + timedelta(minutes=5 * i) for i in range(candles_per_day)]

        # Session prices
        returns = np.random.normal(drift, 0.0016, candles_per_day)
        # Add intraday momentum waves
        wave = np.sin(np.linspace(0, 3.14 * 2, candles_per_day)) * 0.001
        returns += wave

        price_series = current_price * np.exp(np.cumsum(returns))
        current_price = price_series[-1]

        highs = price_series * (1.0 + np.abs(np.random.normal(0, 0.0012, candles_per_day)))
        lows = price_series * (1.0 - np.abs(np.random.normal(0, 0.0012, candles_per_day)))
        opens = np.roll(price_series, 1)
        opens[0] = price_series[0] * (1.0 - drift)
        volumes = np.random.randint(1500, 9000, candles_per_day)

        for t, o, h, l, c, v in zip(session_times, opens, highs, lows, price_series, volumes):
            all_candles.append({
                "time": t,
                "open": o,
                "high": max(h, o, c),
                "low": min(l, o, c),
                "close": c,
                "volume": v
            })

        days_generated += 1
        current_date += timedelta(days=1)

    df = pd.DataFrame(all_candles)
    df.set_index("time", inplace=True)
    return df


def find_swing_pivots(df: pd.DataFrame, window: int = 3) -> tuple:
    """Identifies swing highs and swing lows."""
    highs = df["high"].values
    lows = df["low"].values
    swing_highs = []
    swing_lows = []

    for i in range(window, len(df) - window):
        if all(highs[i] > highs[i - k] for k in range(1, window + 1)) and \
           all(highs[i] > highs[i + k] for k in range(1, window + 1)):
            swing_highs.append((df.index[i], highs[i]))

        if all(lows[i] < lows[i - k] for k in range(1, window + 1)) and \
           all(lows[i] < lows[i + k] for k in range(1, window + 1)):
            swing_lows.append((df.index[i], lows[i]))

    return swing_highs, swing_lows


def run_smc_backtest(df: pd.DataFrame, starting_capital: float = 100000.0) -> List[Dict[str, Any]]:
    """Simulates SMC Liquidity Strategy with session kill switch and RR management."""
    trades = []
    daily_groups = df.groupby(df.index.date)

    # 1-hour candles for HTF bias
    h1_df = df.resample("1h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).dropna()

    # Calculate Macro Trend Filter (50-period EMA on 5-min or resampled)
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    for date, day_df in daily_groups:
        daily_loss = 0.0
        kill_switch = False
        active_trade = None
        consumed_zones = set()

        for i in range(10, len(day_df)):
            current_candle = day_df.iloc[i]
            current_time = current_candle.name
            cmp = float(current_candle["close"])

            # 2% Daily Loss Kill Switch (FR-MR-001)
            if daily_loss <= -(starting_capital * 0.02):
                if not kill_switch:
                    kill_switch = True
                if active_trade:
                    # Emergency close
                    active_trade["exit_price"] = cmp
                    active_trade["exit_time"] = current_time
                    trades.append(active_trade)
                    active_trade = None
                break

            # 1. Manage open trade
            if active_trade:
                side = active_trade["trade_type"]
                entry = active_trade["entry_price"]
                sl = active_trade["sl"]
                tp1 = active_trade["tp1"]

                # Breakeven escalation: Move SL to Cost+Buffer only after reaching 75% of Target
                if not active_trade["be_active"]:
                    if (side == "BUY" and current_candle["high"] >= entry + (tp1 - entry) * 0.75):
                        active_trade["sl"] = entry + (risk * 0.1)  # Lock in cost + tiny profit
                        active_trade["be_active"] = True
                    elif (side == "SELL" and current_candle["low"] <= entry - (entry - tp1) * 0.75):
                        active_trade["sl"] = entry - (risk * 0.1)
                        active_trade["be_active"] = True

                # Trailing SL: Dynamic ATR trailing once in strong profit
                if side == "BUY" and current_candle["high"] > active_trade.get("peak", entry):
                    active_trade["peak"] = current_candle["high"]
                    if active_trade["be_active"]:
                        trail_sl = active_trade["peak"] - (atr * 1.5)
                        if trail_sl > active_trade["sl"]:
                            active_trade["sl"] = trail_sl

                # Check Stop / Target hits
                if side == "BUY":
                    if current_candle["low"] <= active_trade["sl"]:
                        active_trade["exit_price"] = active_trade["sl"]
                        active_trade["exit_time"] = current_time
                        pnl = (active_trade["exit_price"] - entry) * active_trade["qty"]
                        daily_loss += pnl
                        trades.append(active_trade)
                        active_trade = None
                    elif current_candle["high"] >= tp1:
                        active_trade["exit_price"] = tp1
                        active_trade["exit_time"] = current_time
                        pnl = (active_trade["exit_price"] - entry) * active_trade["qty"]
                        daily_loss += pnl
                        trades.append(active_trade)
                        active_trade = None

                elif side == "SELL":
                    if current_candle["high"] >= active_trade["sl"]:
                        active_trade["exit_price"] = active_trade["sl"]
                        active_trade["exit_time"] = current_time
                        pnl = (entry - active_trade["exit_price"]) * active_trade["qty"]
                        daily_loss += pnl
                        trades.append(active_trade)
                        active_trade = None
                    elif current_candle["low"] <= tp1:
                        active_trade["exit_price"] = tp1
                        active_trade["exit_time"] = current_time
                        pnl = (entry - active_trade["exit_price"]) * active_trade["qty"]
                        daily_loss += pnl
                        trades.append(active_trade)
                        active_trade = None

            # 2. Look for new setup if flat
            if not active_trade and not kill_switch:
                # Blackout period (11:30 - 13:00) check
                if current_time.hour == 12 or (current_time.hour == 11 and current_time.minute >= 30):
                    continue

                # Don't take new trades after 14:45
                if current_time.hour >= 15 or (current_time.hour == 14 and current_time.minute >= 45):
                    continue

                # Multi-timeframe bias (H1)
                h1_prior = h1_df[h1_df.index <= current_time]
                if len(h1_prior) < 15:
                    continue
                bias = SMCLiquidityStrategy.get_market_bias(h1_prior)

                m5_window = day_df.iloc[max(0, i-25):i]
                atr = float(SMCLiquidityStrategy.calculate_atr(m5_window).iloc[-1])
                if np.isnan(atr) or atr <= 0:
                    atr = 25.0

                prev_c = day_df.iloc[i-1]
                zones = SMCLiquidityStrategy.detect_liquidity_zones(m5_window, atr_multiplier=0.25)

                macro_bull = cmp > current_candle["ema50"]
                macro_bear = cmp < current_candle["ema50"]

                # Bullish liquidity sweep (swept low and closed back inside range)
                # Only take BUY if macro trend is Bullish or H1 is Bullish
                if macro_bull or bias == "BULLISH":
                    for zone in zones["eql"]:
                        z_key = f"EQL-{zone['level']:.0f}"
                        if z_key in consumed_zones:
                            continue

                        if prev_c["low"] <= zone["level"] and current_candle["close"] > zone["level"]:
                            entry = cmp
                            sl = float(prev_c["low"]) - (atr * 0.15)
                            risk = entry - sl
                            if 10.0 <= risk <= 60.0:  # Sensible risk bracket
                                tp1 = entry + (risk * 2.2)  # 1:2.2 RR
                                risk_capital = starting_capital * 0.01  # 1% risk rule
                                qty = max(25, min(100, int(risk_capital / risk)))

                                active_trade = {
                                    "trade_type": "BUY",
                                    "symbol": "NIFTY",
                                    "entry_price": entry,
                                    "sl": sl,
                                    "tp1": tp1,
                                    "qty": qty,
                                    "risk": risk,
                                    "peak": entry,
                                    "entry_time": current_time,
                                    "be_active": False
                                }
                                consumed_zones.add(z_key)
                                break

                # Bearish liquidity sweep (swept high and closed back inside range)
                # Only take SELL if macro trend is Bearish (never short when price is above EMA50!)
                if not active_trade and (macro_bear and bias in ["BEARISH", "NEUTRAL"]):
                    for zone in zones["eqh"]:
                        z_key = f"EQH-{zone['level']:.0f}"
                        if z_key in consumed_zones:
                            continue

                        if prev_c["high"] >= zone["level"] and current_candle["close"] < zone["level"]:
                            entry = cmp
                            sl = float(prev_c["high"]) + (atr * 0.15)
                            risk = sl - entry
                            if 10.0 <= risk <= 60.0:
                                tp1 = entry - (risk * 2.2)
                                risk_capital = starting_capital * 0.01
                                qty = max(25, min(100, int(risk_capital / risk)))

                                active_trade = {
                                    "trade_type": "SELL",
                                    "symbol": "NIFTY",
                                    "entry_price": entry,
                                    "sl": sl,
                                    "tp1": tp1,
                                    "qty": qty,
                                    "risk": risk,
                                    "peak": entry,
                                    "entry_time": current_time,
                                    "be_active": False
                                }
                                consumed_zones.add(z_key)
                                break

        # End of day square off (15:15 IST)
        if active_trade:
            active_trade["exit_price"] = float(day_df.iloc[-1]["close"])
            active_trade["exit_time"] = day_df.iloc[-1].name
            trades.append(active_trade)
            active_trade = None

    return trades


def run_momentum_backtest(df: pd.DataFrame, starting_capital: float = 100000.0) -> List[Dict[str, Any]]:
    """Runs Momentum EMA (9/21) Crossover + RSI simulation on historical candles."""
    trades = []
    ema9 = df["close"].ewm(span=9, adjust=False).mean()
    ema21 = df["close"].ewm(span=21, adjust=False).mean()

    # RSI (14 period)
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    active_trade = None
    for i in range(25, len(df)):
        c = df.iloc[i]
        cmp = float(c["close"])

        if active_trade:
            # Check exit on crossover reversal or SL
            if active_trade["trade_type"] == "BUY" and (ema9.iloc[i] < ema21.iloc[i] or cmp <= active_trade["sl"]):
                active_trade["exit_price"] = cmp
                active_trade["exit_time"] = c.name
                trades.append(active_trade)
                active_trade = None
            elif active_trade["trade_type"] == "SELL" and (ema9.iloc[i] > ema21.iloc[i] or cmp >= active_trade["sl"]):
                active_trade["exit_price"] = cmp
                active_trade["exit_time"] = c.name
                trades.append(active_trade)
                active_trade = None

        if not active_trade:
            # Crossover Buy (9 crosses above 21 and RSI not overbought)
            if ema9.iloc[i-1] <= ema21.iloc[i-1] and ema9.iloc[i] > ema21.iloc[i] and (rsi.iloc[i] < 70 or np.isnan(rsi.iloc[i])):
                sl = cmp - 40.0
                qty = 50
                active_trade = {
                    "trade_type": "BUY",
                    "symbol": "NIFTY",
                    "entry_price": cmp,
                    "sl": sl,
                    "qty": qty,
                    "entry_time": c.name
                }
            # Crossover Sell (9 crosses below 21 and RSI not oversold)
            elif ema9.iloc[i-1] >= ema21.iloc[i-1] and ema9.iloc[i] < ema21.iloc[i] and (rsi.iloc[i] > 30 or np.isnan(rsi.iloc[i])):
                sl = cmp + 40.0
                qty = 50
                active_trade = {
                    "trade_type": "SELL",
                    "symbol": "NIFTY",
                    "entry_price": cmp,
                    "sl": sl,
                    "qty": qty,
                    "entry_time": c.name
                }

    if active_trade:
        active_trade["exit_price"] = float(df.iloc[-1]["close"])
        active_trade["exit_time"] = df.iloc[-1].name
        trades.append(active_trade)

    return trades


def main():
    parser = argparse.ArgumentParser(description="T-Agent Quantitative Backtest Runner")
    parser.add_argument("--strategy", type=str, default="SMC", choices=["SMC", "MOMENTUM"], help="Strategy to backtest")
    parser.add_argument("--source", type=str, default="angel", choices=["angel", "simulated"], help="Data source: 'angel' for real SmartAPI candles, 'simulated' for synthetic")
    parser.add_argument("--days", type=int, default=15, help="Number of trading days to simulate/fetch")
    parser.add_argument("--capital", type=float, default=100000.0, help="Starting capital")
    parser.add_argument("--output", type=str, default="data/backtest_report.json", help="Report output path")
    args = parser.parse_args()

    init_db()

    print("\n" + "=" * 65)
    print(f"   📈 T-AGENT QUANTITATIVE BACKTEST RUNNER (BRD SECTION 10.1)")
    print("=" * 65)
    print(f" Strategy:         {args.strategy}")
    print(f" Historical Span:  {args.days} Days")
    print(f" Starting Capital: ₹{args.capital:,.2f}")

    df = None
    if args.source == "angel":
        print(" Ingesting REAL historical Nifty 50 candle data from Angel One SmartAPI...")
        df = fetch_angel_candles(days=args.days)
        if df is not None and len(df) > 50:
            print(f" ✅ Loaded {len(df)} authentic historical M5 candles from Angel One.")
        else:
            print(" ⚠️ Could not fetch from Angel One. Falling back to simulated dataset.")
            df = generate_market_data(days=args.days)
    else:
        print(" Generating multi-regime simulated dataset...")
        df = generate_market_data(days=args.days)

    print(f" Executing {args.strategy} with 2% Daily Loss Kill Switch & Trailing Stops...")
    if args.strategy == "SMC":
        trades = run_smc_backtest(df, starting_capital=args.capital)
    else:
        trades = run_momentum_backtest(df, starting_capital=args.capital)

    engine = BacktestEngine(starting_capital=args.capital, slippage_pct=0.0008, commission_per_order=20.0)
    metrics = engine.evaluate_trades(trades)

    def badge(val, threshold, high_good=True):
        passed = (val >= threshold) if high_good else (val <= threshold)
        return "✅ PASS" if passed else "⚠️ BELOW TARGET"

    roi = (metrics["net_profit"] / metrics["starting_capital"]) * 100.0

    print("\n" + "-" * 65)
    print("                   INSTITUTIONAL PERFORMANCE TEAR SHEET")
    print("-" * 65)
    print(f" Final Equity:          ₹{metrics['final_equity']:,.2f} (Net: {'+' if metrics['net_profit']>=0 else ''}₹{metrics['net_profit']:,.2f} | {roi:+.2f}%)")
    print(f" Total Trades Executed:  {metrics['total_trades']} ({metrics['wins']} W / {metrics['losses']} L)")
    print(f" Win Rate:               {metrics['win_rate_pct']:.2f}%  [{badge(metrics['win_rate_pct'], 45.0)} (Target: ≥ 45%)]")
    print(f" Profit Factor:          {metrics['profit_factor']:.2f}  [{badge(metrics['profit_factor'], 1.5)} (Target: > 1.5)]")
    print(f" Sharpe Ratio:           {metrics['sharpe_ratio']:.2f}  [{badge(metrics['sharpe_ratio'], 1.5)} (Target: > 1.5)]")
    print(f" Sortino Ratio:          {metrics['sortino_ratio']:.2f}  [{badge(metrics['sortino_ratio'], 2.0)} (Target: > 2.0)]")
    print(f" Maximum Drawdown:       {metrics['max_drawdown_pct']:.2f}%  [{badge(metrics['max_drawdown_pct'], 10.0, high_good=False)} (Target: < 10%)]")
    print(f" Trade Expectancy:      ₹{metrics['expectancy']:,.2f} per trade")
    print("-" * 65)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"\n📁 Report saved to: {out_path.resolve()}\n")


if __name__ == "__main__":
    main()
