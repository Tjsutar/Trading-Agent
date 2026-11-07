"""
Unit tests for Institutional SafetyGatekeeper (BRD FR-MR-001, FR-MR-002, FR-MR-003, FR-SL-002).
"""
import unittest
from unittest.mock import MagicMock
from core.safety_checks import SafetyGatekeeper
from db.database import init_db
from sqlalchemy import create_engine


class TestSafetyGatekeeper(unittest.TestCase):
    def setUp(self):
        # Configure in-memory database
        test_engine = create_engine("sqlite:///:memory:")
        init_db(test_engine)
        import db.database as db_module
        db_module.ScopedSession.remove()
        db_module.engine = test_engine
        db_module.SessionFactory.configure(bind=test_engine, expire_on_commit=False)
        db_module.ScopedSession.configure(bind=test_engine, expire_on_commit=False)

        self.mock_api = MagicMock()
        self.gatekeeper = SafetyGatekeeper(
            api=self.mock_api,
            starting_capital=100000.0,
            max_daily_loss_pct=0.02  # 2% = ₹2,000 cap
        )

    def tearDown(self):
        import db.database as db_module
        db_module.ScopedSession.remove()

    def test_risk_reward_filter(self):
        # Entry: 100, SL: 90 (Risk: 10), TP: 125 (Reward: 25) -> RR = 2.5 >= 2.0 (PASS)
        valid = self.gatekeeper.validate_risk_reward(entry=100.0, stop_loss=90.0, take_profit=125.0, min_rr=2.0)
        self.assertTrue(valid)

        # Entry: 100, SL: 90 (Risk: 10), TP: 115 (Reward: 15) -> RR = 1.5 < 2.0 (REJECT)
        invalid = self.gatekeeper.validate_risk_reward(entry=100.0, stop_loss=90.0, take_profit=115.0, min_rr=2.0)
        self.assertFalse(invalid)

    def test_kill_switch_trigger(self):
        # Within limit: loss is -1,000
        safe = self.gatekeeper.check_max_daily_loss(current_unrealized_pnl=-1000.0)
        self.assertTrue(safe)
        self.assertFalse(self.gatekeeper.kill_switch_active)

        # Exceeds limit: loss is -2,500 <= -2,000 cap -> Trigger Kill Switch
        breached = self.gatekeeper.check_max_daily_loss(current_unrealized_pnl=-2500.0)
        self.assertFalse(breached)
        self.assertTrue(self.gatekeeper.kill_switch_active)

    def test_volatility_position_sizing(self):
        # Capital = 100,000, Risk = 1% = 1,000. Entry = 100, SL = 80 (dist = 20).
        # Multiplier = 50. Risk per lot = 20 * 50 = 1,000.
        # Expected lots: 1 lot (50 qty) when VIX <= 25.
        qty_normal = self.gatekeeper.calculate_position_size(
            account_balance=100000.0,
            entry_price=100.0,
            stop_loss=80.0,
            risk_pct=0.01,
            lot_size_multiplier=50,
            india_vix=15.0
        )
        self.assertEqual(qty_normal, 50)

        # When Capital = 200,000, 2 lots (100 qty). If VIX > 25 (e.g. 28.0), reduced by 50% to 1 lot (50 qty)
        qty_high_vix = self.gatekeeper.calculate_position_size(
            account_balance=200000.0,
            entry_price=100.0,
            stop_loss=80.0,
            risk_pct=0.01,
            lot_size_multiplier=50,
            india_vix=28.0
        )
        self.assertEqual(qty_high_vix, 50)


if __name__ == "__main__":
    unittest.main()

