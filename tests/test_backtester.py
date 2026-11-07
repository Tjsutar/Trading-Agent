"""
Unit tests for Institutional Backtest Engine (BRD Section 10.1).
"""
import unittest
from core.backtester import BacktestEngine


class TestBacktestEngine(unittest.TestCase):
    def setUp(self):
        self.engine = BacktestEngine(
            starting_capital=100000.0,
            slippage_pct=0.001,
            commission_per_order=20.0
        )

    def test_performance_metrics_calculation(self):
        # 10 trades: 6 wins, 4 losses
        trades = [
            {"entry_price": 100.0, "exit_price": 120.0, "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 95.0,  "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 125.0, "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 93.0,  "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 115.0, "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 92.0,  "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 130.0, "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 118.0, "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 94.0,  "qty": 50, "trade_type": "BUY"},
            {"entry_price": 100.0, "exit_price": 122.0, "qty": 50, "trade_type": "BUY"},
        ]

        metrics = self.engine.evaluate_trades(trades)
        self.assertEqual(metrics["total_trades"], 10)
        self.assertEqual(metrics["wins"], 6)
        self.assertEqual(metrics["losses"], 4)
        self.assertEqual(metrics["win_rate_pct"], 60.0)
        self.assertGreater(metrics["profit_factor"], 1.5)
        self.assertGreater(metrics["net_profit"], 0)
        self.assertGreater(metrics["sharpe_ratio"], 1.0)
        self.assertGreater(metrics["sortino_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
