"""
Quantitative Parameter Optimization Engine (BRD Section 10.1 & FR-SL-003).
Performs grid-search optimization across EMA pairs, Stop Loss distances, and RSI thresholds
using real Angel One historical exchange candles.
Identifies parameter sets that maximize Sharpe Ratio, Sortino Ratio, and Win Rate.
"""
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backtester import BacktestEngine
from tools.run_backtest import fetch_angel_candles, generate_market_data
from utils.logger import get_logger

logger = get_logger("Optimizer")


def simulate_momentum_params(
    df: pd.DataFrame,
    fast_ema: int,
    slow_ema: int,
    sl_points: float,
    rsi_buy_max: float,
    rsi_sell_min: float,
    starting_capital: float = 100000.0
) -> List[Dict[str, Any]]:
    """Simulates a single parameter combination on historical candle data."""
    trades = []
    ema_fast = df["close"].ewm(span=fast_ema, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow_ema, adjust=False).mean()

    # RSI (14 period)
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    active_trade = None
    for i in range(max(slow_ema, 25), len(df)):
        c = df.iloc[i]
        cmp = float(c["close"])

        if active_trade:
            # Check exit
            side = active_trade["trade_type"]
            entry = active_trade["entry_price"]
            sl = active_trade["sl"]

            # Breakeven escalation: If in profit by 1.5x SL distance, move SL to entry + 5
            if not active_trade.get("be_active"):
                if (side == "BUY" and cmp >= entry + (sl_points * 1.5)) or \
                   (side == "SELL" and cmp <= entry - (sl_points * 1.5)):
                    active_trade["sl"] = entry + (5.0 if side == "BUY" else -5.0)
                    active_trade["be_active"] = True

            # Exit on crossover reversal or stop loss hit
            if side == "BUY" and (ema_fast.iloc[i] < ema_slow.iloc[i] or cmp <= active_trade["sl"]):
                active_trade["exit_price"] = cmp
                active_trade["exit_time"] = c.name
                trades.append(active_trade)
                active_trade = None
            elif side == "SELL" and (ema_fast.iloc[i] > ema_slow.iloc[i] or cmp >= active_trade["sl"]):
                active_trade["exit_price"] = cmp
                active_trade["exit_time"] = c.name
                trades.append(active_trade)
                active_trade = None

        if not active_trade:
            current_rsi = rsi.iloc[i]
            # Fast crosses above Slow
            if ema_fast.iloc[i-1] <= ema_slow.iloc[i-1] and ema_fast.iloc[i] > ema_slow.iloc[i]:
                if np.isnan(current_rsi) or current_rsi < rsi_buy_max:
                    active_trade = {
                        "trade_type": "BUY",
                        "symbol": "NIFTY",
                        "entry_price": cmp,
                        "sl": cmp - sl_points,
                        "qty": 50,
                        "entry_time": c.name,
                        "be_active": False
                    }
            # Fast crosses below Slow
            elif ema_fast.iloc[i-1] >= ema_slow.iloc[i-1] and ema_fast.iloc[i] < ema_slow.iloc[i]:
                if np.isnan(current_rsi) or current_rsi > rsi_sell_min:
                    active_trade = {
                        "trade_type": "SELL",
                        "symbol": "NIFTY",
                        "entry_price": cmp,
                        "sl": cmp + sl_points,
                        "qty": 50,
                        "entry_time": c.name,
                        "be_active": False
                    }

    if active_trade:
        active_trade["exit_price"] = float(df.iloc[-1]["close"])
        active_trade["exit_time"] = df.iloc[-1].name
        trades.append(active_trade)

    return trades


def run_grid_search(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Runs a grid search across EMA pairs, stop-loss distances, and RSI thresholds."""
    ema_pairs = [
        (5, 13),
        (9, 21),
        (13, 34),
        (20, 50)
    ]
    sl_distances = [25.0, 35.0, 45.0, 60.0]
    rsi_configs = [
        (70.0, 30.0),
        (65.0, 35.0),
        (75.0, 25.0)
    ]

    engine = BacktestEngine(starting_capital=100000.0, slippage_pct=0.0008, commission_per_order=20.0)
    results = []

    total_runs = len(ema_pairs) * len(sl_distances) * len(rsi_configs)
    print(f"Running grid search over {total_runs} parameter combinations...\n")

    run_idx = 0
    for fast_ema, slow_ema in ema_pairs:
        for sl in sl_distances:
            for rsi_buy, rsi_sell in rsi_configs:
                run_idx += 1
                trades = simulate_momentum_params(
                    df,
                    fast_ema=fast_ema,
                    slow_ema=slow_ema,
                    sl_points=sl,
                    rsi_buy_max=rsi_buy,
                    rsi_sell_min=rsi_sell
                )
                metrics = engine.evaluate_trades(trades)
                
                results.append({
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "sl_points": sl,
                    "rsi_buy_max": rsi_buy,
                    "rsi_sell_min": rsi_sell,
                    "total_trades": metrics["total_trades"],
                    "win_rate_pct": metrics["win_rate_pct"],
                    "net_profit": metrics["net_profit"],
                    "roi_pct": round((metrics["net_profit"] / 100000.0) * 100.0, 2),
                    "profit_factor": metrics["profit_factor"],
                    "sharpe_ratio": metrics["sharpe_ratio"],
                    "sortino_ratio": metrics["sortino_ratio"],
                    "max_drawdown_pct": metrics["max_drawdown_pct"],
                    "expectancy": metrics["expectancy"]
                })

    # Sort results by Net Profit and Sharpe Ratio
    results.sort(key=lambda x: (x["net_profit"], x["sharpe_ratio"]), reverse=True)
    return results


def main():
    print("=" * 70)
    print("   🔬 QUANTITATIVE PARAMETER OPTIMIZATION GRID SEARCH")
    print("=" * 70)

    print("Fetching authentic historical candles from Angel One SmartAPI...")
    df = fetch_angel_candles(days=15)
    if df is None or len(df) < 50:
        print("Using simulated market dataset fallback...")
        df = generate_market_data(days=30)
    else:
        print(f"Loaded {len(df)} authentic historical M5 candles from Angel One.\n")

    results = run_grid_search(df)

    print("-" * 70)
    print("                 TOP 5 OPTIMIZED PARAMETER COMBINATIONS")
    print("-" * 70)
    print(f"{'Rank':4s} | {'EMA Pair':8s} | {'SL (Pts)':8s} | {'RSI (B/S)':9s} | {'Trades':6s} | {'Win Rate':8s} | {'Net P&L':12s} | {'Sharpe':6s} | {'Sortino':7s}")
    print("-" * 70)

    for idx, r in enumerate(results[:5], 1):
        ema_str = f"{r['fast_ema']}/{r['slow_ema']}"
        rsi_str = f"{int(r['rsi_buy_max'])}/{int(r['rsi_sell_min'])}"
        pnl_str = f"{'+' if r['net_profit']>=0 else ''}₹{r['net_profit']:,.2f} ({r['roi_pct']:+.1f}%)"
        print(f"#{idx:<3d} | {ema_str:8s} | {r['sl_points']:8.1f} | {rsi_str:9s} | {r['total_trades']:<6d} | {r['win_rate_pct']:6.1f}% | {pnl_str:12s} | {r['sharpe_ratio']:6.2f} | {r['sortino_ratio']:7.2f}")

    print("-" * 70)
    best = results[0]
    print(f"\n🏆 BEST CONFIGURATION IDENTIFIED:")
    print(f"   • Fast EMA:        {best['fast_ema']}")
    print(f"   • Slow EMA:        {best['slow_ema']}")
    print(f"   • Stop Loss:       {best['sl_points']} points")
    print(f"   • RSI Filters:     Buy < {best['rsi_buy_max']}, Sell > {best['rsi_sell_min']}")
    print(f"   • Net Return:      +{best['roi_pct']}% (₹{best['net_profit']:,.2f})")
    print(f"   • Win Rate:        {best['win_rate_pct']}%")
    print(f"   • Sharpe Ratio:    {best['sharpe_ratio']}")
    print(f"   • Sortino Ratio:   {best['sortino_ratio']}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()

