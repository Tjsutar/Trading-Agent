"""
Institutional Backtesting and Quantitative Performance Engine.
Complies with BRD Section 10.1 (Sharpe > 1.5, Sortino > 2.0, Drawdown < 10%, Win Rate >= 45%).
Calculates institutional metrics:
  - Sharpe Ratio
  - Sortino Ratio (downside risk focused)
  - Maximum Drawdown (%)
  - Profit Factor (Gross Profit / Gross Loss)
  - Win Rate (%) & Total Trades
  - Expectancy ($/INR per trade)
"""
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd


class BacktestEngine:
    def __init__(
        self,
        starting_capital: float = 100000.0,
        risk_free_rate: float = 0.06,  # 6% annual risk-free rate
        slippage_pct: float = 0.001,   # 0.1% slippage
        commission_per_order: float = 20.0  # ₹20 flat broker charge
    ):
        self.starting_capital = starting_capital
        self.risk_free_rate = risk_free_rate
        self.slippage_pct = slippage_pct
        self.commission_per_order = commission_per_order

    def evaluate_trades(self, trade_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Takes executed trade records and computes institutional performance metrics.
        Each trade dict should contain:
          {'entry_price': ..., 'exit_price': ..., 'qty': ..., 'trade_type': 'BUY'/'SELL', 'entry_time': ..., 'exit_time': ...}
        """
        if not trade_list:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "net_profit": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0
            }

        pnls = []
        equity = self.starting_capital
        equity_curve = [equity]

        gross_profit = 0.0
        gross_loss = 0.0
        wins = 0
        losses = 0

        for t in trade_list:
            entry = float(t['entry_price'])
            exit_p = float(t['exit_price'])
            qty = int(t.get('qty', 1))
            t_type = t.get('trade_type', 'BUY').upper()

            # Apply slippage
            if t_type == 'BUY':
                adj_entry = entry * (1.0 + self.slippage_pct)
                adj_exit = exit_p * (1.0 - self.slippage_pct)
                trade_pnl = (adj_exit - adj_entry) * qty
            else:
                adj_entry = entry * (1.0 - self.slippage_pct)
                adj_exit = exit_p * (1.0 + self.slippage_pct)
                trade_pnl = (adj_entry - adj_exit) * qty

            # Deduct commission (entry + exit)
            trade_pnl -= (self.commission_per_order * 2)

            pnls.append(trade_pnl)
            equity += trade_pnl
            equity_curve.append(equity)

            if trade_pnl > 0:
                gross_profit += trade_pnl
                wins += 1
            else:
                gross_loss += abs(trade_pnl)
                losses += 1

        total_trades = len(pnls)
        win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        net_profit = equity - self.starting_capital

        # Calculate returns series for Sharpe & Sortino
        returns = np.array(pnls) / self.starting_capital
        daily_rf = self.risk_free_rate / 252.0

        excess_returns = returns - daily_rf
        mean_return = np.mean(returns) if len(returns) > 0 else 0.0
        std_return = np.std(returns) if len(returns) > 0 else 0.0

        # Sharpe (annualized assuming ~250 trading days)
        sharpe = (np.mean(excess_returns) / std_return * np.sqrt(252)) if std_return > 0 else 0.0

        # Sortino (downside deviation only)
        downside_returns = returns[returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 0 else 0.0
        sortino = (np.mean(excess_returns) / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

        # Maximum Drawdown calculation
        eq_series = pd.Series(equity_curve)
        rolling_max = eq_series.cummax()
        drawdowns = (eq_series - rolling_max) / rolling_max
        max_drawdown = float(abs(drawdowns.min())) * 100.0

        expectancy = net_profit / total_trades if total_trades > 0 else 0.0

        return {
            "starting_capital": self.starting_capital,
            "final_equity": round(equity, 2),
            "net_profit": round(net_profit, 2),
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "expectancy": round(expectancy, 2)
        }

