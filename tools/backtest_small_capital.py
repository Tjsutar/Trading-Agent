"""
Institutional Small Capital Backtest Simulator (₹5,000 Tier).
Simulates realistic 1-lot Nifty Option Buying across 3 months of authentic Angel One candles (Aug-Nov 2026).
Enforces:
  1. Starting Capital: ₹5,000
  2. Fixed 1-Lot Position Sizing (25 qty Nifty options)
  3. Strict Max Daily Loss Kill Switch: -₹350 (session halts on drawdown)
  4. Max 2 trades per session to prevent fee erosion
  5. Minimum 1:2 Risk-Reward ratio
  6. Breakeven escalation (+1R profit moves SL to cost)
  7. Slippage & Brokerage (₹20 per order)
"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backtester import BacktestEngine
from db.database import init_db
from utils.logger import get_logger

logger = get_logger("SmallCapBacktest")


def simulate_small_capital_momentum(df: pd.DataFrame, starting_capital: float = 5000.0) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Simulates Momentum Option Buying on real 5-min candles for ₹5,000 capital.
    """
    # Technical indicators
    ema9 = df["close"].ewm(span=9, adjust=False).mean()
    ema21 = df["close"].ewm(span=21, adjust=False).mean()
    ema50 = df["close"].ewm(span=50, adjust=False).mean()

    # RSI (14 period)
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    trades = []
    equity = starting_capital
    daily_groups = df.groupby(df.index.date)

    LOT_SIZE = 25  # Standard Nifty option contract lot size
    OPTION_DELTA = 0.50  # ATM option delta (~0.50)
    MAX_DAILY_LOSS = -350.0  # ~7% of 5k account (hard daily stop)
    MAX_TRADES_PER_DAY = 2
    BROKERAGE_PER_ORDER = 20.0
    SLIPPAGE_PTS = 1.0  # 1 point slippage on option premium

    days_traded = 0
    profitable_days = 0
    losing_days = 0

    for date, day_df in daily_groups:
        days_traded += 1
        daily_pnl = 0.0
        daily_trade_count = 0
        kill_switch_active = False
        active_trade = None

        for i in range(1, len(day_df)):
            current_candle = day_df.iloc[i]
            prev_candle = day_df.iloc[i-1]
            current_time = current_candle.name
            cmp = float(current_candle["close"])

            # 1. Check Daily Kill Switch
            if daily_pnl <= MAX_DAILY_LOSS:
                if not kill_switch_active:
                    kill_switch_active = True
                if active_trade:
                    # Emergency market exit
                    exit_opt = active_trade["entry_opt"] - (abs(cmp - active_trade["entry_spot"]) * OPTION_DELTA)
                    trade_pnl = (exit_opt - active_trade["entry_opt"]) * LOT_SIZE - (BROKERAGE_PER_ORDER * 2)
                    daily_pnl += trade_pnl
                    equity += trade_pnl
                    active_trade["exit_price"] = exit_opt
                    active_trade["exit_time"] = current_time
                    active_trade["pnl"] = trade_pnl
                    trades.append(active_trade)
                    active_trade = None
                break

            # 2. Manage Active Trade
            if active_trade:
                side = active_trade["trade_type"]
                entry_spot = active_trade["entry_spot"]
                entry_opt = active_trade["entry_opt"]
                sl_opt = active_trade["sl_opt"]
                tp_opt = active_trade["tp_opt"]

                # Current estimated option price based on spot delta
                if side == "BUY_CE":
                    spot_diff = cmp - entry_spot
                    est_opt_price = entry_opt + (spot_diff * OPTION_DELTA) - SLIPPAGE_PTS
                else:  # BUY_PE
                    spot_diff = entry_spot - cmp
                    est_opt_price = entry_opt + (spot_diff * OPTION_DELTA) - SLIPPAGE_PTS

                # Breakeven escalation: If option moves +15 points in profit, move SL to entry + 2
                if not active_trade["be_active"]:
                    if est_opt_price >= entry_opt + 15.0:
                        active_trade["sl_opt"] = entry_opt + 2.0
                        active_trade["be_active"] = True

                # Check Target or Stop
                exit_triggered = False
                exit_reason = ""
                final_exit_opt = est_opt_price

                if est_opt_price <= active_trade["sl_opt"]:
                    exit_triggered = True
                    exit_reason = "BREAKEVEN_HIT" if active_trade["be_active"] else "STOP_LOSS_HIT"
                    final_exit_opt = active_trade["sl_opt"]
                elif est_opt_price >= tp_opt:
                    exit_triggered = True
                    exit_reason = "TARGET_HIT"
                    final_exit_opt = tp_opt
                # Time exit at 15:15 IST
                elif current_time.hour == 15 and current_time.minute >= 15:
                    exit_triggered = True
                    exit_reason = "TIME_SQUAREOFF"
                    final_exit_opt = est_opt_price

                if exit_triggered:
                    trade_pnl = (final_exit_opt - entry_opt) * LOT_SIZE - (BROKERAGE_PER_ORDER * 2)
                    daily_pnl += trade_pnl
                    equity += trade_pnl
                    active_trade["exit_price"] = final_exit_opt
                    active_trade["exit_time"] = current_time
                    active_trade["pnl"] = trade_pnl
                    active_trade["reason"] = exit_reason
                    trades.append(active_trade)
                    active_trade = None

            # 3. Look for Entry Setup (if flat and daily limit not reached)
            if not active_trade and not kill_switch_active and daily_trade_count < MAX_TRADES_PER_DAY:
                # Blackout period & market open/close noise filters
                if current_time.hour < 9 or (current_time.hour == 9 and current_time.minute < 30):
                    continue
                if current_time.hour >= 14 and current_time.minute >= 45:
                    continue
                if current_time.hour == 12:  # Midday lull
                    continue

                idx_loc = df.index.get_loc(current_time)
                if idx_loc < 25:
                    continue

                c_ema9 = ema9.iloc[idx_loc]
                c_ema21 = ema21.iloc[idx_loc]
                c_ema50 = ema50.iloc[idx_loc]
                p_ema9 = ema9.iloc[idx_loc - 1]
                p_ema21 = ema21.iloc[idx_loc - 1]
                c_rsi = rsi.iloc[idx_loc]

                # Option pricing estimate (~₹100 ATM contract)
                est_entry_premium = 90.0
                SL_PTS = 16.0  # 16 pts stop = ₹400 risk
                TP_PTS = 36.0  # 36 pts target = ₹900 profit (1:2.25 RR)

                # Bullish Momentum Setup (9 crosses above 21 + Trend above 50 EMA + RSI < 65)
                if p_ema9 <= p_ema21 and c_ema9 > c_ema21 and cmp > c_ema50 and (c_rsi < 68 or np.isnan(c_rsi)):
                    active_trade = {
                        "trade_type": "BUY_CE",
                        "symbol": f"NIFTY_CE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": est_entry_premium,
                        "sl_opt": est_entry_premium - SL_PTS,
                        "tp_opt": est_entry_premium + TP_PTS,
                        "qty": LOT_SIZE,
                        "entry_time": current_time,
                        "be_active": False
                    }
                    daily_trade_count += 1

                # Bearish Momentum Setup (9 crosses below 21 + Trend below 50 EMA + RSI > 35)
                elif p_ema9 >= p_ema21 and c_ema9 < c_ema21 and cmp < c_ema50 and (c_rsi > 32 or np.isnan(c_rsi)):
                    active_trade = {
                        "trade_type": "BUY_PE",
                        "symbol": f"NIFTY_PE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": est_entry_premium,
                        "sl_opt": est_entry_premium - SL_PTS,
                        "tp_opt": est_entry_premium + TP_PTS,
                        "qty": LOT_SIZE,
                        "entry_time": current_time,
                        "be_active": False
                    }
                    daily_trade_count += 1

        if daily_pnl > 0:
            profitable_days += 1
        elif daily_pnl < 0:
            losing_days += 1

    summary = {
        "starting_capital": starting_capital,
        "ending_capital": equity,
        "net_pnl": equity - starting_capital,
        "roi_pct": round(((equity - starting_capital) / starting_capital) * 100.0, 2),
        "days_traded": days_traded,
        "profitable_days": profitable_days,
        "losing_days": losing_days,
        "day_win_rate_pct": round((profitable_days / max(1, days_traded)) * 100.0, 2)
    }

    return trades, summary


def main():
    print("=" * 68)
    print("   💰 T-AGENT SMALL CAPITAL BACKTEST (₹5,000 ACCOUNT TIER)")
    print("   3-Month Real Historical Data: Aug 10, 2026 – Nov 8, 2026")
    print("=" * 68)

    data_file = Path("data/nifty_3m_5min.csv")
    if not data_file.exists():
        print("Dataset not found. Downloading 3-month authentic data from Angel One...")
        from tools.download_historical_data import download_3m_data
        df = download_3m_data()
    else:
        df = pd.read_csv(data_file)
        df["time"] = pd.to_datetime(df["time"])
        df.set_index("time", inplace=True)
        print(f"Loaded {len(df)} authentic historical candles from: {data_file.name}")

    START_CAP = 5000.0
    trades, summary = simulate_small_capital_momentum(df, starting_capital=START_CAP)

    # Performance metrics
    total_trades = len(trades)
    wins = len([t for t in trades if t.get("pnl", 0) > 0])
    losses = len([t for t in trades if t.get("pnl", 0) <= 0])
    win_rate = (wins / max(1, total_trades)) * 100.0

    gross_profit = sum([t["pnl"] for t in trades if t["pnl"] > 0])
    gross_loss = abs(sum([t["pnl"] for t in trades if t["pnl"] <= 0]))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    expectancy = (summary["net_pnl"] / total_trades) if total_trades > 0 else 0.0

    # Drawdown calculation
    pnls = [t["pnl"] for t in trades]
    eq_curve = [START_CAP]
    cur = START_CAP
    for p in pnls:
        cur += p
        eq_curve.append(cur)
    eq_s = pd.Series(eq_curve)
    max_dd = float(abs(((eq_s - eq_s.cummax()) / eq_s.cummax()).min()) * 100.0)

    print("\n" + "-" * 68)
    print("                  SMALL CAPITAL PERFORMANCE TEAR SHEET")
    print("-" * 68)
    print(f" Initial Account Capital: ₹{START_CAP:,.2f}")
    print(f" Final Account Balance:   ₹{summary['ending_capital']:,.2f} ({'+' if summary['net_pnl']>=0 else ''}₹{summary['net_pnl']:,.2f} | {summary['roi_pct']:+.1f}%)")
    print(f" Total Trades Executed:   {total_trades} ({wins} Wins / {losses} Losses)")
    print(f" Trade Win Rate:          {win_rate:.1f}%")
    print(f" Profitable Days:         {summary['profitable_days']} Days ({summary['day_win_rate_pct']:.1f}% Win Days)")
    print(f" Profit Factor:           {profit_factor:.2f}")
    print(f" Average Expectancy:     ₹{expectancy:+,.2f} per trade")
    print(f" Maximum Drawdown:        {max_dd:.1f}%")
    print(f" Brokerage & Fees Deducted: ₹{total_trades * 40:,.2f} (Included in Net)")
    print("-" * 68)

    print("\n[ Recent 8 Executed Trades Sample ]")
    for t in trades[-8:]:
        pnl = t.get("pnl", 0.0)
        mark = "🟢 WIN" if pnl > 0 else "🔴 LOSS"
        print(f"  • {t['entry_time'].strftime('%b %d, %H:%M')} | {t['trade_type']} | Entry: ₹{t['entry_opt']:.1f} -> Exit: ₹{t['exit_price']:.1f} | P&L: {pnl:+,.0f} [{mark}] ({t['reason']})")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    main()
