"""
Unit tests for PositionManager and MomentumStrategy Trailing Stop-Loss (TSL).
"""
import unittest
import sys
import os
import datetime
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.position_manager import PositionManager
from strategies.momentum_strategy import MomentumStrategy


class MockAPI:
    def placeOrder(self, params):
        return "TEST_ORDER_ID"

    def ltpData(self, exchange, symbol, token):
        return {"status": True, "data": {"ltp": 100.0}}


class TestTSL(unittest.TestCase):

    def test_momentum_trailing_sl_escalation(self):
        api = MockAPI()
        bot = MomentumStrategy(api=api, token_loader=None, dry_run=True, starting_capital=15000.0)

        # Simulate entered position @ ₹90
        bot.active_position = {
            "order_id": "POS-TEST",
            "symbol": "NIFTY_CE_25000",
            "token": "12345",
            "entry_price": 90.0,
            "highest_price": 90.0,
            "sl_price": 74.0,  # -16 pts initial stop
            "target_price": 126.0,
            "be_active": False,
            "qty": 65
        }

        # 1. Price at 95 (+5 pts) -> SL remains 74.0
        bot.get_option_ltp = lambda sym, tok: 95.0
        self.assertFalse(bot.check_trailing_stop())
        self.assertEqual(bot.active_position["sl_price"], 74.0)
        self.assertFalse(bot.active_position["be_active"])

        # 2. Price surges to 102 (+12 pts) -> Breakeven triggers & Dynamic TSL locks peak - 8 = 94.0
        bot.get_option_ltp = lambda sym, tok: 102.0
        self.assertFalse(bot.check_trailing_stop())
        self.assertTrue(bot.active_position["be_active"])
        self.assertEqual(bot.active_position["sl_price"], 94.0)  # 102 - 8 = 94.0 (Locks +4 pts!)

        # 3. Price rallies to 118 (+28 pts peak) -> Dynamic TSL trails (118 - 8 = 110.0)
        bot.get_option_ltp = lambda sym, tok: 118.0
        self.assertFalse(bot.check_trailing_stop())
        self.assertEqual(bot.active_position["highest_price"], 118.0)
        self.assertEqual(bot.active_position["sl_price"], 110.0)  # Locked +20 pts profit!

        # 4. Price retraces to 109.5 -> Hits trailing stop! (<= 110.0)
        bot.get_option_ltp = lambda sym, tok: 109.5
        exited = bot.check_trailing_stop()
        self.assertTrue(exited)
        self.assertIsNone(bot.active_position)  # Position safely closed with locked profit!


if __name__ == "__main__":
    unittest.main()
