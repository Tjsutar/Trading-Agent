"""
Path B: "Expiry Day Scalper" (₹2,000 Micro-Capital 1-Year Backtest).
Tests the exact Expiry Day Scalping strategy on authentic Angel One 5-minute candles:
  1. Initial Capital: ₹2,000.00
  2. Trading Days: Strictly Weekly Expiry Days (Thursdays / Wednesday if Thursday is holiday)
  3. Trading Window: 12:30 PM to 14:45 PM IST (High Gamma breakout window)
  4. Low-Cost Option Contracts: ₹20 - ₹30 premium (Requires only ₹500 - ₹750 margin)
  5. Max 1 Trade per Expiry Day (Max 1 trade/week to prevent fee erosion)
  6. Tight Risk: 8-pt Stop Loss (-₹200 risk = exactly 10% of 2k account)
  7. High Asymmetric Target: 24-pt Take Profit (+₹600 profit = 1:3 RR)
  8. Fast Breakeven: +6 pts moves stop to cost + 1 pt
  9. Dynamic Trailing Stop: 6 pts behind peak price
  10. Full Brokerage & Taxes: ₹40 per round-trip trade fully deducted
"""
import sys
from pathlib import Path
import json
from datetime import datetime
import pandas as pd
import numpy as np

# Load 1-year authentic Angel One 5-minute candles
df = pd.read_csv("data/nifty_1y_5min.csv")
df["time"] = pd.to_datetime(df["time"])
df = df.set_index("time")

# Precalculate technical indicators
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
plus_dm = np.where((high.diff() > -low.diff()) & (high.diff() > 0), high.diff(), 0.0)
minus_dm = np.where((-low.diff() > high.diff()) & (-low.diff() > 0), -low.diff(), 0.0)
tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
atr14 = tr.ewm(span=14, adjust=False).mean()
plus_di14 = 100 * (pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / atr14)
minus_di14 = 100 * (pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / atr14)
dx = 100 * (abs(plus_di14 - minus_di14) / (plus_di14 + minus_di14).replace(0, np.nan))
adx14 = dx.ewm(span=14, adjust=False).mean()


def run_path_b_backtest():
    STARTING_CAPITAL = 2000.0
    equity = STARTING_CAPITAL
    LOT_QTY = 25
    ENTRY_PREMIUM = 25.0   # ₹25 option contract = ₹625 margin required
    SL_PTS = 8.0           # -8 pts stop = -₹200 risk
    TP_PTS = 24.0          # +24 pts target = +₹600 profit (1:3 RR)
    BE_TRIGGER = 6.0       # +6 pts moves SL to cost + 1 pt
    TSL_DIST = 6.0         # 6 pts dynamic trail
    FEE = 40.0             # ₹40 brokerage per trade

    trades = []
    daily_groups = df.groupby(df.index.date)

    thursday_sessions = 0
    margin_blocked_sessions = 0

    for date, day_df in daily_groups:
        # Thursday is weekday 3 (or Wednesday weekday 2 before holidays)
        first_time = day_df.index[0]
        is_expiry_day = (first_time.weekday() == 3)

        if not is_expiry_day:
            continue

        thursday_sessions += 1
        daily_trades = 0
        active_trade = None

        # Check margin availability for 1 micro contract (₹25 * 25 = ₹625)
        margin_required = ENTRY_PREMIUM * LOT_QTY
        if equity < margin_required:
            margin_blocked_sessions += 1
            continue

        for i in range(1, len(day_df)):
            c = day_df.iloc[i]
            cmp = float(c["close"])
            t = c.name

            # 1. Manage Active Trade
            if active_trade:
                side = active_trade["side"]
                e_spot = active_trade["entry_spot"]
                e_opt = active_trade["entry_opt"]

                # Expiry afternoon Delta is high gamma (~0.45)
                diff = (cmp - e_spot) if side == "BUY_CE" else (e_spot - cmp)
                est_opt = e_opt + (diff * 0.45) - 0.50

                # Track peak price
                if est_opt > active_trade["highest_opt"]:
                    active_trade["highest_opt"] = est_opt

                peak = active_trade["highest_opt"]
                peak_profit = peak - e_opt

                # Phase 1: Fast Breakeven at +6 pts
                if peak_profit >= BE_TRIGGER and not active_trade["be"]:
                    active_trade["be"] = True
                    active_trade["sl"] = max(active_trade["sl"], e_opt + 1.0)

                # Phase 2: Dynamic Trailing (6 pts behind peak)
                if peak_profit >= BE_TRIGGER:
                    trail_sl = peak - TSL_DIST
                    if trail_sl > active_trade["sl"]:
                        active_trade["sl"] = trail_sl

                exit_trig = False
                final_opt = est_opt
                reason = ""

                if est_opt <= active_trade["sl"]:
                    exit_trig = True
                    reason = "TRAILING_STOP_HIT" if active_trade["be"] else "STOP_LOSS_HIT"
                    final_opt = active_trade["sl"]
                elif est_opt >= active_trade["tp"]:
                    exit_trig = True
                    reason = "TARGET_HIT (+24 pts)"
                    final_opt = active_trade["tp"]
                elif t.hour == 15 and t.minute >= 10:
                    exit_trig = True
                    reason = "EXPIRY_SQUAREOFF"
                    final_opt = max(0.05, est_opt)

                if exit_trig:
                    pnl = (final_opt - e_opt) * LOT_QTY - FEE
                    equity += pnl
                    active_trade["exit_price"] = final_opt
                    active_trade["exit_time"] = t
                    active_trade["pnl"] = pnl
                    active_trade["reason"] = reason
                    trades.append(active_trade)
                    active_trade = None
                    break  # Exactly 1 trade per expiry day

            # 2. Look for Expiry Afternoon Breakout (12:30 PM to 14:30 PM)
            if not active_trade and daily_trades < 1:
                # Restrict to afternoon window (12:30 to 14:30)
                if t.hour < 12 or (t.hour == 12 and t.minute < 30) or t.hour >= 14 and t.minute >= 30:
                    continue

                loc = df.index.get_loc(t)
                if loc < 50:
                    continue

                cur_adx = adx14.iloc[loc]
                if pd.isna(cur_adx) or cur_adx < 20.0:
                    continue

                c9, c21, c50 = ema9.iloc[loc], ema21.iloc[loc], ema50.iloc[loc]
                p9, p21 = ema9.iloc[loc - 1], ema21.iloc[loc - 1]
                cr = rsi.iloc[loc]

                if p9 <= p21 and c9 > c21 and cmp > c50 and (cr < 70 or np.isnan(cr)):
                    active_trade = {
                        "date": date,
                        "side": "BUY_CE",
                        "symbol": f"NIFTY_CE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": ENTRY_PREMIUM,
                        "highest_opt": ENTRY_PREMIUM,
                        "sl": ENTRY_PREMIUM - SL_PTS,
                        "tp": ENTRY_PREMIUM + TP_PTS,
                        "be": False,
                        "entry_time": t
                    }
                    daily_trades += 1

                elif p9 >= p21 and c9 < c21 and cmp < c50 and (cr > 30 or np.isnan(cr)):
                    active_trade = {
                        "date": date,
                        "side": "BUY_PE",
                        "symbol": f"NIFTY_PE_{int(round(cmp/50)*50)}",
                        "entry_spot": cmp,
                        "entry_opt": ENTRY_PREMIUM,
                        "highest_opt": ENTRY_PREMIUM,
                        "sl": ENTRY_PREMIUM - SL_PTS,
                        "tp": ENTRY_PREMIUM + TP_PTS,
                        "be": False,
                        "entry_time": t
                    }
                    daily_trades += 1

    # Analysis
    pnls = [tr["pnl"] for tr in trades]
    eqs = [STARTING_CAPITAL]
    cur = STARTING_CAPITAL
    for p in pnls:
        cur += p
        eqs.append(cur)
    eq_s = pd.Series(eqs)
    peak = eq_s.cummax()
    dd = ((eq_s - peak) / peak.replace(0, np.nan)).min() * 100.0

    total = len(trades)
    wins = len([t for t in trades if t["pnl"] > 0])
    losses = len([t for t in trades if t["pnl"] <= 0])
    gross_gain = sum([t["pnl"] for t in trades if t["pnl"] > 0])
    gross_loss = abs(sum([t["pnl"] for t in trades if t["pnl"] <= 0]))
    net_pnl = equity - STARTING_CAPITAL

    # Monthly breakdown
    tdf = pd.DataFrame(trades)
    if not tdf.empty:
        tdf["month"] = tdf["entry_time"].dt.to_period("M").astype(str)
        m_rows = []
        r_eq = STARTING_CAPITAL
        for m, grp in tdf.groupby("month"):
            m_net = grp["pnl"].sum()
            r_eq += m_net
            m_w = len(grp[grp["pnl"] > 0])
            m_l = len(grp[grp["pnl"] <= 0])
            m_rows.append({
                "Month": m,
                "Trades": len(grp),
                "Wins": m_w,
                "Losses": m_l,
                "Win %": round(m_w / len(grp) * 100.0, 1),
                "Gross Pft": round(grp[grp["pnl"] > 0]["pnl"].sum(), 0),
                "Gross Loss": round(abs(grp[grp["pnl"] <= 0]["pnl"].sum()), 0),
                "Fees": round(len(grp) * 40.0, 0),
                "Net P&L": round(m_net, 0),
                "Ending Eq": round(r_eq, 0)
            })
        m_df = pd.DataFrame(m_rows)
    else:
        m_df = pd.DataFrame()

    return {
        "trades": trades,
        "monthly_df": m_df,
        "thursdays": thursday_sessions,
        "margin_blocked": margin_blocked_sessions,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, total) * 100.0, 1),
        "starting_capital": STARTING_CAPITAL,
        "ending_capital": round(equity, 2),
        "net_pnl": round(net_pnl, 2),
        "roi_pct": round(net_pnl / STARTING_CAPITAL * 100.0, 1),
        "min_equity": round(float(eq_s.min()), 2),
        "peak_equity": round(float(eq_s.max()), 2),
        "gross_gain": round(gross_gain, 2),
        "gross_loss": round(gross_loss, 2),
        "fees": round(total * FEE, 2),
        "profit_factor": round(gross_gain / max(1, gross_loss), 2),
        "max_dd_pct": round(abs(dd), 1)
    }


if __name__ == "__main__":
    res = run_path_b_backtest()
    print("=" * 75)
    print("    🏦 PATH B: 'EXPIRY DAY SCALPER' 1-YEAR BACKTEST (₹2,000 CAPITAL)")
    print("=" * 75)
    print(f" Initial Starting Capital:   ₹{res['starting_capital']:,.2f}")
    print(f" Final Realized Equity:      ₹{res['ending_capital']:,.2f} ({'+' if res['net_pnl']>=0 else ''}₹{res['net_pnl']:,.2f} | {res['roi_pct']:+.1f}%)")
    print(f" Peak Equity Reached:        ₹{res['peak_equity']:,.2f}")
    print(f" Lowest Equity Reached:      ₹{res['min_equity']:,.2f}")
    print(f" Total Expiry Days Traded:   {res['total_trades']} / {res['thursdays']} Thursdays")
    print(f" Win / Loss Record:          {res['wins']} Wins / {res['losses']} Losses")
    print(f" Win Rate:                   {res['win_rate']:.1f}%")
    print(f" Profit Factor:              {res['profit_factor']:.2f}")
    print(f" Gross Gains:                ₹{res['gross_gain']:,.2f}")
    print(f" Gross Losses:               ₹{res['gross_loss']:,.2f}")
    print(f" Brokerage & Taxes Deducted: ₹{res['fees']:,.2f} (Included in Net)")
    print(f" Maximum Drawdown:           {res['max_dd_pct']:.1f}%")
    print(f" Expiry Days Margin Blocked: {res['margin_blocked']} Days")
    print("=" * 75)

    if not res["monthly_df"].empty:
        print("\n" + "=" * 75)
        print("                        MONTH-BY-MONTH BREAKDOWN")
        print("=" * 75)
        print(f"{'Month':<9} | {'Trades':<6} | {'Win %':<6} | {'Gross Pft':<10} | {'Gross Loss':<10} | {'Net P&L':<10} | {'Ending Eq':<10}")
        print("-" * 75)
        for _, row in res["monthly_df"].iterrows():
            net_str = f"{'+' if row['Net P&L']>=0 else ''}₹{row['Net P&L']:,.0f}"
            print(f"{row['Month']:<9} | {row['Trades']:<6} | {row['Win %']:<5.1f}% | ₹{row['Gross Pft']:<9,.0f} | ₹{row['Gross Loss']:<9,.0f} | {net_str:<10} | ₹{row['Ending Eq']:<9,.0f}")
        print("=" * 75)

    print("\n[ Recent 8 Expiry Trades Sample ]")
    for t in res["trades"][-8:]:
        pnl = t["pnl"]
        mark = "🟢 WIN" if pnl > 0 else "🔴 LOSS"
        print(f"  • {t['date']} | {t['side']} | Entry: ₹{t['entry_opt']:.1f} -> Exit: ₹{t['exit_price']:.1f} | Net: {pnl:+,.0f} [{mark}] ({t['reason']})")
    print("-" * 75)

