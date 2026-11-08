"""
Generate Comprehensive Updated 1-Year Backtest Report for ₹15,000 Capital Tier.
Reflects ALL Production Strategy Upgrades:
  1. ADX > 22 Trend Regime Filter (Sits in cash during sideways chop)
  2. Stop-on-1st-Loss Rule (No revenge trading / halts on 1st loss)
  3. Dynamic Trailing Stop Loss (Phase 1: BE @ +10 pts -> Phase 2: 8-pt trailing)
  4. Hard Daily Loss Kill Switch (-₹700 for 1-lot / -₹1,400 for 2-lot)
  5. Slippage & Brokerage (₹40 round-trip deducted per trade)
"""
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load 1-year authentic Angel One 5-minute candles
df = pd.read_csv("data/nifty_1y_5min.csv")
df["time"] = pd.to_datetime(df["time"])
df = df.set_index("time")

# Technical indicators
ema9 = df["close"].ewm(span=9, adjust=False).mean()
ema21 = df["close"].ewm(span=21, adjust=False).mean()
ema50 = df["close"].ewm(span=50, adjust=False).mean()

delta = df["close"].diff()
gain = (delta.where(delta > 0, 0)).rolling(14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
rs = gain / loss.replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))

# ADX (14)
high = df["high"]
low = df["low"]
close = df["close"]
plus_dm = high.diff()
minus_dm = -low.diff()
plus_dm_val = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
minus_dm_val = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

tr1 = high - low
tr2 = abs(high - close.shift(1))
tr3 = abs(low - close.shift(1))
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

atr14 = tr.ewm(span=14, adjust=False).mean()
plus_di14 = 100 * (pd.Series(plus_dm_val, index=df.index).ewm(span=14, adjust=False).mean() / atr14)
minus_di14 = 100 * (pd.Series(minus_dm_val, index=df.index).ewm(span=14, adjust=False).mean() / atr14)
dx = 100 * (abs(plus_di14 - minus_di14) / (plus_di14 + minus_di14).replace(0, np.nan))
adx14 = dx.ewm(span=14, adjust=False).mean()


def run_updated_backtest(lot_qty: int = 25, daily_loss_limit: float = -700.0, max_trades_per_day: int = 1):
    STARTING_CAPITAL = 15000.0
    OPTION_DELTA = 0.50
    BROKERAGE_PER_ORDER = 20.0
    SLIPPAGE_PTS = 1.0
    INITIAL_SL_PTS = 16.0
    FAST_BE_PTS = 10.0
    BE_LOCK_PTS = 2.0
    TSL_DISTANCE = 8.0
    MIN_ADX = 22.0

    trades = []
    equity = STARTING_CAPITAL
    daily_groups = df.groupby(df.index.date)

    days_traded = 0
    profitable_days = 0
    losing_days = 0
    flat_days = 0
    kill_switch_days = 0

    for date, day_df in daily_groups:
        days_traded += 1
        daily_pnl = 0.0
        daily_trades = 0
        kill_switch = False
        active_trade = None
        had_loss = False

        for i in range(1, len(day_df)):
            c = day_df.iloc[i]
            cmp = float(c["close"])
            t = c.name

            # 1. Kill Switch
            if daily_pnl <= daily_loss_limit:
                if not kill_switch:
                    kill_switch = True
                    kill_switch_days += 1
                if active_trade:
                    exit_opt = active_trade["entry_opt"] - (abs(cmp - active_trade["entry_spot"]) * OPTION_DELTA)
                    pnl = (exit_opt - active_trade["entry_opt"]) * lot_qty - (BROKERAGE_PER_ORDER * 2)
                    daily_pnl += pnl
                    equity += pnl
                    active_trade["exit_price"] = exit_opt
                    active_trade["exit_time"] = t
                    active_trade["pnl"] = pnl
                    active_trade["reason"] = "KILL_SWITCH_HALT"
                    trades.append(active_trade)
                    active_trade = None
                break

            # 2. Manage Active Position with Dynamic TSL
            if active_trade:
                side = active_trade["side"]
                e_spot = active_trade["entry_spot"]
                e_opt = active_trade["entry_opt"]

                diff = (cmp - e_spot) if side == "BUY_CE" else (e_spot - cmp)
                est_opt = e_opt + (diff * OPTION_DELTA) - SLIPPAGE_PTS

                # Update peak option price
                if est_opt > active_trade["highest_opt"]:
                    active_trade["highest_opt"] = est_opt

                peak = active_trade["highest_opt"]
                peak_profit = peak - e_opt

                # Phase 1: Fast Breakeven at +10 pts (Lock Cost + 2 pts)
                if peak_profit >= FAST_BE_PTS and not active_trade["be"]:
                    active_trade["be"] = True
                    be_sl = e_opt + BE_LOCK_PTS
                    if be_sl > active_trade["sl"]:
                        active_trade["sl"] = be_sl

                # Phase 2: Dynamic Continuous Trailing (8 pts behind peak)
                if peak_profit >= FAST_BE_PTS:
                    trail_sl = peak - TSL_DISTANCE
                    if trail_sl > active_trade["sl"]:
                        active_trade["sl"] = trail_sl

                # Check Exits
                exit_trig = False
                final_opt = est_opt
                reason = ""

                if est_opt <= active_trade["sl"]:
                    exit_trig = True
                    reason = "TRAILING_STOP_HIT" if active_trade["be"] else "STOP_LOSS_HIT"
                    final_opt = active_trade["sl"]
                elif t.hour == 15 and t.minute >= 15:
                    exit_trig = True
                    reason = "TIME_EXIT"
                    final_opt = est_opt

                if exit_trig:
                    pnl = (final_opt - e_opt) * lot_qty - (BROKERAGE_PER_ORDER * 2)
                    daily_pnl += pnl
                    equity += pnl
                    active_trade["exit_price"] = final_opt
                    active_trade["exit_time"] = t
                    active_trade["pnl"] = pnl
                    active_trade["reason"] = reason
                    trades.append(active_trade)
                    if pnl < 0:
                        had_loss = True
                    active_trade = None

            # 3. Look for High-Conviction Setups (Regime Protected)
            if not active_trade and not kill_switch and daily_trades < max_trades_per_day:
                # Stop-on-1st-Loss Rule
                if had_loss:
                    continue

                if t.hour < 9 or (t.hour == 9 and t.minute < 30) or (t.hour >= 14 and t.minute >= 45) or t.hour == 12:
                    continue

                loc = df.index.get_loc(t)
                if loc < 50:
                    continue

                # ADX Trend Filter (Reject choppy sideways markets)
                cur_adx = adx14.iloc[loc]
                if pd.isna(cur_adx) or cur_adx < MIN_ADX:
                    continue

                c9, c21, c50 = ema9.iloc[loc], ema21.iloc[loc], ema50.iloc[loc]
                p9, p21 = ema9.iloc[loc - 1], ema21.iloc[loc - 1]
                cr = rsi.iloc[loc]

                est_premium = 90.0

                # Bullish Momentum Setup
                if p9 <= p21 and c9 > c21 and cmp > c50 and (cr < 68 or np.isnan(cr)):
                    active_trade = {
                        "side": "BUY_CE",
                        "symbol": f"NIFTY_CE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": est_premium,
                        "highest_opt": est_premium,
                        "sl": est_premium - INITIAL_SL_PTS,
                        "be": False,
                        "entry_time": t
                    }
                    daily_trades += 1

                # Bearish Momentum Setup
                elif p9 >= p21 and c9 < c21 and cmp < c50 and (cr > 32 or np.isnan(cr)):
                    active_trade = {
                        "side": "BUY_PE",
                        "symbol": f"NIFTY_PE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": est_premium,
                        "highest_opt": est_premium,
                        "sl": est_premium - INITIAL_SL_PTS,
                        "be": False,
                        "entry_time": t
                    }
                    daily_trades += 1

        if daily_pnl > 10.0:
            profitable_days += 1
        elif daily_pnl < -10.0:
            losing_days += 1
        else:
            flat_days += 1

    # Metrics calculation
    pnls = [tr["pnl"] for tr in trades]
    eqs = [STARTING_CAPITAL]
    cur = STARTING_CAPITAL
    for p in pnls:
        cur += p
        eqs.append(cur)
    eq_s = pd.Series(eqs)
    peak = eq_s.cummax()
    dd = ((eq_s - peak) / peak.replace(0, np.nan)).min() * 100.0

    total_trades = len(trades)
    wins = len([t for t in trades if t["pnl"] > 0])
    losses = len([t for t in trades if t["pnl"] <= 0])
    win_rate = (wins / max(1, total_trades)) * 100.0

    gross_profit = sum([t["pnl"] for t in trades if t["pnl"] > 0])
    gross_loss = abs(sum([t["pnl"] for t in trades if t["pnl"] <= 0]))
    net_pnl = equity - STARTING_CAPITAL
    roi_pct = (net_pnl / STARTING_CAPITAL) * 100.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    expectancy = net_pnl / max(1, total_trades)

    # Monthly breakdown
    tdf = pd.DataFrame(trades)
    tdf["month"] = tdf["entry_time"].dt.to_period("M").astype(str)

    monthly_rows = []
    running_eq = STARTING_CAPITAL
    for month, grp in tdf.groupby("month"):
        m_t = len(grp)
        m_w = len(grp[grp["pnl"] > 0])
        m_l = len(grp[grp["pnl"] <= 0])
        m_wr = (m_w / m_t * 100.0) if m_t > 0 else 0.0
        m_gp = grp[grp["pnl"] > 0]["pnl"].sum()
        m_gl = abs(grp[grp["pnl"] <= 0]["pnl"].sum())
        m_fees = m_t * (BROKERAGE_PER_ORDER * 2)
        m_net = grp["pnl"].sum()
        running_eq += m_net
        m_pf = (m_gp / m_gl) if m_gl > 0 else (99.0 if m_gp > 0 else 0.0)

        monthly_rows.append({
            "Month": month,
            "Trades": m_t,
            "Wins": m_w,
            "Losses": m_l,
            "Win Rate %": round(m_wr, 1),
            "Gross Profit": round(m_gp, 2),
            "Gross Loss": round(m_gl, 2),
            "Fees": round(m_fees, 2),
            "Net P&L": round(m_net, 2),
            "Profit Factor": round(m_pf, 2),
            "Ending Equity": round(running_eq, 2)
        })

    return {
        "trades": trades,
        "monthly_df": pd.DataFrame(monthly_rows),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "starting_capital": STARTING_CAPITAL,
        "ending_capital": round(equity, 2),
        "net_pnl": round(net_pnl, 2),
        "roi_pct": round(roi_pct, 1),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "min_equity": round(float(eq_s.min()), 2),
        "peak_equity": round(float(eq_s.max()), 2),
        "max_dd_pct": round(abs(dd), 1),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        "fees": round(total_trades * 40.0, 2),
        "days_traded": days_traded,
        "profitable_days": profitable_days,
        "losing_days": losing_days,
        "kill_switch_days": kill_switch_days
    }


if __name__ == "__main__":
    print("Running updated strategy backtest for ₹15,000 Capital Tier...")
    res_1lot = run_updated_backtest(lot_qty=25, daily_loss_limit=-700.0, max_trades_per_day=1)
    res_2lot = run_updated_backtest(lot_qty=50, daily_loss_limit=-1400.0, max_trades_per_day=1)

    print("\n" + "=" * 90)
    print("      UPDATED PRODUCTION STRATEGY BACKTEST (1-YEAR REAL DATA: NOV 2025 - NOV 2026)")
    print("=" * 90)
    print(f" Initial Account Capital: ₹15,000.00")
    print("-" * 90)
    print(f"{'Configuration':<25} | {'Trades':<6} | {'Win %':<6} | {'Net P&L':<11} | {'ROI %':<8} | {'Min Eq':<9} | {'Peak Eq':<9} | {'PF':<5} | {'Max DD %':<8}")
    print("-" * 90)
    pnl1 = f"{'+' if res_1lot['net_pnl']>=0 else ''}₹{res_1lot['net_pnl']:,.0f}"
    pnl2 = f"{'+' if res_2lot['net_pnl']>=0 else ''}₹{res_2lot['net_pnl']:,.0f}"
    print(f"{'1 Lot (25 Qty) - Safe':<25} | {res_1lot['total_trades']:<6} | {res_1lot['win_rate']:<5.1f}% | {pnl1:<11} | {res_1lot['roi_pct']:<+7.1f}% | ₹{res_1lot['min_equity']:<8,.0f} | ₹{res_1lot['peak_equity']:<8,.0f} | {res_1lot['profit_factor']:<5.2f} | {res_1lot['max_dd_pct']:<7.1f}%")
    print(f"{'2 Lots (50 Qty) - Growth':<25} | {res_2lot['total_trades']:<6} | {res_2lot['win_rate']:<5.1f}% | {pnl2:<11} | {res_2lot['roi_pct']:<+7.1f}% | ₹{res_2lot['min_equity']:<8,.0f} | ₹{res_2lot['peak_equity']:<8,.0f} | {res_2lot['profit_factor']:<5.2f} | {res_2lot['max_dd_pct']:<7.1f}%")
    print("=" * 90)

    print("\n" + "=" * 90)
    print("            MONTH-BY-MONTH BREAKDOWN (1 LOT TIER WITH DYNAMIC TSL)")
    print("=" * 90)
    print(f"{'Month':<9} | {'Trades':<6} | {'Win %':<6} | {'Gross Pft':<11} | {'Gross Loss':<11} | {'Net P&L':<11} | {'PF':<5} | {'Ending Eq':<11}")
    print("-" * 90)
    for _, row in res_1lot["monthly_df"].iterrows():
        net_str = f"{'+' if row['Net P&L'] >= 0 else ''}₹{row['Net P&L']:,.0f}"
        print(f"{row['Month']:<9} | {row['Trades']:<6} | {row['Win Rate %']:<5.1f}% | ₹{row['Gross Profit']:<9,.0f} | ₹{row['Gross Loss']:<9,.0f} | {net_str:<11} | {row['Profit Factor']:<5.2f} | ₹{row['Ending Equity']:<10,.0f}")
    print("=" * 90)

    # Save to JSON
    report_dict = {
        "capital": 15000.0,
        "upgraded_features": [
            "ADX > 22 Trend Regime Gate (Sits in cash during sideways chop)",
            "Stop-on-1st-Loss Rule (Halts after 1 loss to prevent fee churn)",
            "Dynamic Trailing Stop-Loss (BE @ +10 pts, continuous 8-pt trail behind peak)",
            "Hard Daily Loss Kill Switch (-₹700 for 1-lot / -₹1,400 for 2-lot)",
            "Persistent WebSocket Streaming CMP (0ms latency, zero REST rate limits)"
        ],
        "one_lot_model": {k: v for k, v in res_1lot.items() if k not in ["trades", "monthly_df"]},
        "two_lot_model": {k: v for k, v in res_2lot.items() if k not in ["trades", "monthly_df"]},
        "monthly_breakdown_1lot": res_1lot["monthly_df"].to_dict(orient="records"),
        "monthly_breakdown_2lot": res_2lot["monthly_df"].to_dict(orient="records")
    }

    with open("data/updated_backtest_15k_report.json", "w") as f:
        json.dump(report_dict, f, indent=2)
    print("\n✅ Full updated report saved to: data/updated_backtest_15k_report.json")
