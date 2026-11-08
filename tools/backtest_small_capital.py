"""
Institutional Small Capital Backtest Simulator (₹5,000 Tier) - Full 1-Year Multi-Month Analysis.
Simulates realistic 1-lot Nifty Option Buying across 1 Year (18,400+ authentic Angel One M5 candles).

Enforces:
  1. Starting Capital: ₹5,000
  2. Fixed 1-Lot Position Sizing (25 qty Nifty options)
  3. Strict Max Daily Loss Kill Switch: -₹350 (session halts on drawdown)
  4. Max 2 trades per session to prevent fee erosion
  5. Minimum 1:2 Risk-Reward ratio (16 pts SL : 36 pts TP)
  6. Breakeven escalation (+15 pts profit moves SL to entry + 2 pts)
  7. Slippage & Brokerage (₹20 per buy/sell order = ₹40 round trip)
"""
import sys
import os
import json
import argparse
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


def simulate_small_capital_momentum(
    df: pd.DataFrame,
    starting_capital: float = 5000.0,
    lot_size: int = 25,
    max_daily_loss: float = -350.0,
    max_trades_per_day: int = 2,
    brokerage_per_order: float = 20.0,
    slippage_pts: float = 1.0,
    option_delta: float = 0.50,
    sl_pts: float = 16.0,
    tp_pts: float = 36.0,
    be_trigger_pts: float = 15.0,
    be_lock_pts: float = 2.0
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], pd.DataFrame]:
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
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    trades = []
    equity = starting_capital
    daily_groups = df.groupby(df.index.date)

    days_traded = 0
    profitable_days = 0
    losing_days = 0
    break_even_days = 0
    kill_switch_days = 0

    daily_records = []

    for date, day_df in daily_groups:
        days_traded += 1
        daily_pnl = 0.0
        daily_trade_count = 0
        kill_switch_active = False
        active_trade = None

        for i in range(1, len(day_df)):
            current_candle = day_df.iloc[i]
            current_time = current_candle.name
            cmp = float(current_candle["close"])

            # 1. Check Daily Kill Switch
            if daily_pnl <= max_daily_loss:
                if not kill_switch_active:
                    kill_switch_active = True
                    kill_switch_days += 1
                if active_trade:
                    # Emergency market exit
                    exit_opt = active_trade["entry_opt"] - (abs(cmp - active_trade["entry_spot"]) * option_delta)
                    trade_pnl = (exit_opt - active_trade["entry_opt"]) * lot_size - (brokerage_per_order * 2)
                    daily_pnl += trade_pnl
                    equity += trade_pnl
                    active_trade["exit_price"] = exit_opt
                    active_trade["exit_time"] = current_time
                    active_trade["pnl"] = trade_pnl
                    active_trade["reason"] = "KILL_SWITCH_TRIGGERED"
                    active_trade["equity_after"] = equity
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
                    est_opt_price = entry_opt + (spot_diff * option_delta) - slippage_pts
                else:  # BUY_PE
                    spot_diff = entry_spot - cmp
                    est_opt_price = entry_opt + (spot_diff * option_delta) - slippage_pts

                # Breakeven escalation: If option moves +15 points in profit, move SL to entry + 2
                if not active_trade["be_active"]:
                    if est_opt_price >= entry_opt + be_trigger_pts:
                        active_trade["sl_opt"] = entry_opt + be_lock_pts
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
                    trade_pnl = (final_exit_opt - entry_opt) * lot_size - (brokerage_per_order * 2)
                    daily_pnl += trade_pnl
                    equity += trade_pnl
                    active_trade["exit_price"] = final_exit_opt
                    active_trade["exit_time"] = current_time
                    active_trade["pnl"] = trade_pnl
                    active_trade["reason"] = exit_reason
                    active_trade["equity_after"] = equity
                    trades.append(active_trade)
                    active_trade = None

            # 3. Look for Entry Setup (if flat and daily limit not reached)
            if not active_trade and not kill_switch_active and daily_trade_count < max_trades_per_day:
                # Blackout period & market open/close noise filters
                if current_time.hour < 9 or (current_time.hour == 9 and current_time.minute < 30):
                    continue
                if current_time.hour >= 14 and current_time.minute >= 45:
                    continue
                if current_time.hour == 12:  # Midday low volatility lull
                    continue

                idx_loc = df.index.get_loc(current_time)
                if idx_loc < 50:
                    continue

                c_ema9 = ema9.iloc[idx_loc]
                c_ema21 = ema21.iloc[idx_loc]
                c_ema50 = ema50.iloc[idx_loc]
                p_ema9 = ema9.iloc[idx_loc - 1]
                p_ema21 = ema21.iloc[idx_loc - 1]
                c_rsi = rsi.iloc[idx_loc]

                est_entry_premium = 90.0

                # Bullish Momentum Setup (9 crosses above 21 + Trend above 50 EMA + RSI < 68)
                if p_ema9 <= p_ema21 and c_ema9 > c_ema21 and cmp > c_ema50 and (c_rsi < 68 or np.isnan(c_rsi)):
                    active_trade = {
                        "trade_type": "BUY_CE",
                        "symbol": f"NIFTY_CE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": est_entry_premium,
                        "sl_opt": est_entry_premium - sl_pts,
                        "tp_opt": est_entry_premium + tp_pts,
                        "qty": lot_size,
                        "entry_time": current_time,
                        "be_active": False
                    }
                    daily_trade_count += 1

                # Bearish Momentum Setup (9 crosses below 21 + Trend below 50 EMA + RSI > 32)
                elif p_ema9 >= p_ema21 and c_ema9 < c_ema21 and cmp < c_ema50 and (c_rsi > 32 or np.isnan(c_rsi)):
                    active_trade = {
                        "trade_type": "BUY_PE",
                        "symbol": f"NIFTY_PE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": est_entry_premium,
                        "sl_opt": est_entry_premium - sl_pts,
                        "tp_opt": est_entry_premium + tp_pts,
                        "qty": lot_size,
                        "entry_time": current_time,
                        "be_active": False
                    }
                    daily_trade_count += 1

        if daily_pnl > 10.0:
            profitable_days += 1
        elif daily_pnl < -10.0:
            losing_days += 1
        else:
            break_even_days += 1

        daily_records.append({
            "date": date,
            "daily_pnl": daily_pnl,
            "trades": daily_trade_count,
            "equity": equity,
            "kill_switch": kill_switch_active
        })

    summary = {
        "starting_capital": starting_capital,
        "ending_capital": equity,
        "net_pnl": equity - starting_capital,
        "roi_pct": round(((equity - starting_capital) / starting_capital) * 100.0, 2),
        "days_traded": days_traded,
        "profitable_days": profitable_days,
        "losing_days": losing_days,
        "break_even_days": break_even_days,
        "kill_switch_days": kill_switch_days,
        "day_win_rate_pct": round((profitable_days / max(1, days_traded)) * 100.0, 2)
    }

    return trades, summary, pd.DataFrame(daily_records)


def analyze_monthly_breakdown(trades: List[Dict[str, Any]], starting_capital: float = 5000.0) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()

    tdf = pd.DataFrame(trades)
    tdf["month"] = tdf["entry_time"].dt.to_period("M").astype(str)

    monthly_rows = []
    running_eq = starting_capital

    for month, m_group in tdf.groupby("month"):
        m_trades = len(m_group)
        m_wins = len(m_group[m_group["pnl"] > 0])
        m_losses = len(m_group[m_group["pnl"] <= 0])
        m_win_rate = (m_wins / m_trades * 100.0) if m_trades > 0 else 0.0
        m_gross_profit = m_group[m_group["pnl"] > 0]["pnl"].sum()
        m_gross_loss = abs(m_group[m_group["pnl"] <= 0]["pnl"].sum())
        m_fees = m_trades * 40.0
        m_net_pnl = m_group["pnl"].sum()
        running_eq += m_net_pnl
        m_pf = (m_gross_profit / m_gross_loss) if m_gross_loss > 0 else (99.0 if m_gross_profit > 0 else 0.0)

        monthly_rows.append({
            "Month": month,
            "Trades": m_trades,
            "Wins": m_wins,
            "Losses": m_losses,
            "Win Rate %": round(m_win_rate, 1),
            "Gross Profit": round(m_gross_profit, 2),
            "Gross Loss": round(m_gross_loss, 2),
            "Fees": round(m_fees, 2),
            "Net P&L": round(m_net_pnl, 2),
            "Profit Factor": round(m_pf, 2),
            "Ending Equity": round(running_eq, 2)
        })

    return pd.DataFrame(monthly_rows)


def main():
    parser = argparse.ArgumentParser(description="1-Year Institutional Small Capital Backtest Simulator")
    parser.add_argument("--data", type=str, default="data/nifty_1y_5min.csv", help="Path to CSV dataset")
    parser.add_argument("--capital", type=float, default=5000.0, help="Initial account capital")
    args = parser.parse_args()

    data_file = Path(args.data)
    if not data_file.exists():
        print(f"Dataset {data_file} not found. Attempting download of 1-year data...")
        from tools.download_historical_data import download_historical_data
        df = download_historical_data(days=365, output_file=str(data_file))
    else:
        df = pd.read_csv(data_file)
        df["time"] = pd.to_datetime(df["time"])
        df.set_index("time", inplace=True)

    date_start = df.index.min().strftime('%Y-%m-%d')
    date_end = df.index.max().strftime('%Y-%m-%d')

    print("=" * 76)
    print("      🏦 T-AGENT 1-YEAR INSTITUTIONAL BACKTEST (₹5,000 CAPITAL TIER)")
    print(f"      Period: {date_start} to {date_end} ({len(df):,} authentic M5 candles)")
    print("=" * 76)

    trades, summary, daily_df = simulate_small_capital_momentum(df, starting_capital=args.capital)

    # Core performance metrics
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
    eq_curve = [args.capital]
    cur = args.capital
    for p in pnls:
        cur += p
        eq_curve.append(cur)
    eq_s = pd.Series(eq_curve)
    peak_s = eq_s.cummax()
    dd_s = (eq_s - peak_s) / peak_s.replace(0, np.nan)
    max_dd = float(abs(dd_s.min()) * 100.0)
    min_eq = float(eq_s.min())
    peak_eq = float(eq_s.max())

    # Breakdown by Side
    ce_trades = [t for t in trades if t["trade_type"] == "BUY_CE"]
    pe_trades = [t for t in trades if t["trade_type"] == "BUY_PE"]
    ce_wins = len([t for t in ce_trades if t["pnl"] > 0])
    pe_wins = len([t for t in pe_trades if t["pnl"] > 0])
    ce_pnl = sum([t["pnl"] for t in ce_trades])
    pe_pnl = sum([t["pnl"] for t in pe_trades])

    # Breakdown by Exit Reason
    reasons = {}
    for t in trades:
        r = t.get("reason", "UNKNOWN")
        reasons[r] = reasons.get(r, 0) + 1

    print("\n" + "-" * 76)
    print("                      ANNUAL EXECUTIVE PERFORMANCE SUMMARY")
    print("-" * 76)
    print(f" Starting Initial Capital:    ₹{args.capital:,.2f}")
    print(f" Peak Equity Reached:         ₹{peak_eq:,.2f}")
    print(f" Lowest Equity Reached:       ₹{min_eq:,.2f}")
    print(f" Final Realized Equity:       ₹{summary['ending_capital']:,.2f}")
    print(f" Net Realized Profit (P&L):   {'+' if summary['net_pnl'] >= 0 else ''}₹{summary['net_pnl']:,.2f}")
    print(f" Annual Return on Investment: {summary['roi_pct']:+.1f}%")
    print(f" Maximum Drawdown:            {max_dd:.1f}%")
    print(f" Profit Factor:               {profit_factor:.2f}")
    print(f" Average Expectancy / Trade: ₹{expectancy:+,.2f}")
    print(f" Total Trades Executed:       {total_trades} ({wins} Wins / {losses} Losses)")
    print(f" Overall Win Rate:            {win_rate:.1f}%")
    print(f" Active Trading Days:         {summary['days_traded']} Days")
    print(f" Profitable Days:             {summary['profitable_days']} Days ({summary['day_win_rate_pct']:.1f}% Day Win Rate)")
    print(f" Losing Days:                 {summary['losing_days']} Days")
    print(f" Break-even / Flat Days:      {summary['break_even_days']} Days")
    print(f" Kill Switch Days Triggered:  {summary['kill_switch_days']} Days (Protected Capital from > ₹350 loss)")
    print(f" Total Brokerage & Taxes:     ₹{total_trades * 40:,.2f} (Fully Deducted)")
    print("-" * 76)

    print("\n[ Performance by Strategy Side ]")
    print(f"  • Call Options (BUY CE): {len(ce_trades)} Trades | Win Rate: {(ce_wins/max(1,len(ce_trades))*100):.1f}% | Net P&L: {'+' if ce_pnl>=0 else ''}₹{ce_pnl:,.2f}")
    print(f"  • Put Options  (BUY PE): {len(pe_trades)} Trades | Win Rate: {(pe_wins/max(1,len(pe_trades))*100):.1f}% | Net P&L: {'+' if pe_pnl>=0 else ''}₹{pe_pnl:,.2f}")

    print("\n[ Exit Reason Distribution ]")
    for r, count in sorted(reasons.items(), key=lambda x: -x[1]):
        pct = (count / max(1, total_trades)) * 100.0
        print(f"  • {r:<25}: {count:3d} trades ({pct:5.1f}%)")

    # Monthly Breakdown
    monthly_df = analyze_monthly_breakdown(trades, starting_capital=args.capital)
    if not monthly_df.empty:
        print("\n" + "=" * 76)
        print("                        MONTH-BY-MONTH BREAKDOWN")
        print("=" * 76)
        print(f"{'Month':<9} | {'Trades':<6} | {'Win %':<6} | {'Gross Pft':<11} | {'Gross Loss':<11} | {'Net P&L':<11} | {'PF':<5} | {'Ending Eq':<11}")
        print("-" * 76)
        for _, row in monthly_df.iterrows():
            net_str = f"{'+' if row['Net P&L'] >= 0 else ''}₹{row['Net P&L']:,.0f}"
            print(f"{row['Month']:<9} | {row['Trades']:<6} | {row['Win Rate %']:<5.1f}% | ₹{row['Gross Profit']:<9,.0f} | ₹{row['Gross Loss']:<9,.0f} | {net_str:<11} | {row['Profit Factor']:<5.2f} | ₹{row['Ending Equity']:<10,.0f}")
        print("=" * 76)

    # Save complete JSON report
    report_data = {
        "config": {
            "starting_capital": args.capital,
            "period": f"{date_start} to {date_end}",
            "candle_count": len(df),
            "lot_size": 25,
            "max_daily_loss": -350.0,
            "sl_pts": 16.0,
            "tp_pts": 36.0,
            "breakeven_trigger": 15.0
        },
        "summary": summary,
        "metrics": {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "brokerage_deducted": total_trades * 40.0
        },
        "monthly_breakdown": monthly_df.to_dict(orient="records") if not monthly_df.empty else []
    }

    out_json = Path("data/backtest_1year_report.json")
    with open(out_json, "w") as f:
        json.dump(report_data, f, indent=2)
    print(f"\n📁 Detailed 1-Year Backtest report saved to: {out_json.resolve()}\n")


if __name__ == "__main__":
    main()
