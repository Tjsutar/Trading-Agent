"""
Unit tests for the Database and Repository Layer (BRD FR-SP-001).
"""
import os
import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from db.models import Base
from db.database import init_db
from db.repository import TradeRepository


class TestTradeRepository(unittest.TestCase):
    def setUp(self):
        # Initialize an in-memory SQLite database for testing
        test_engine = create_engine("sqlite:///:memory:")
        init_db(test_engine)
        
        # Patch the database engine and session
        import db.database as db_module
        db_module.ScopedSession.remove()
        db_module.engine = test_engine
        db_module.SessionFactory.configure(bind=test_engine, expire_on_commit=False)
        db_module.ScopedSession.configure(bind=test_engine, expire_on_commit=False)

    def tearDown(self):
        import db.database as db_module
        db_module.ScopedSession.remove()

    def test_record_signal(self):
        sig = TradeRepository.record_signal(
            strategy_name="Momentum_EMA_RSI",
            symbol="NIFTY",
            timeframe="M5",
            signal_type="BUY",
            confidence=85.0,
            entry_price=22500.0,
            stop_loss=22450.0,
            take_profit=22600.0,
            risk_reward=2.0,
            executed=True
        )
        self.assertIsNotNone(sig.id)
        self.assertEqual(sig.symbol, "NIFTY")
        self.assertEqual(sig.signal_type, "BUY")
        self.assertTrue(sig.executed)

    def test_create_and_close_trade(self):
        trade = TradeRepository.create_trade(
            order_id="ORD-1001",
            strategy_name="920_Straddle",
            symbol="NIFTY26FEB22500CE",
            trade_type="SELL",
            lot_size=50,
            entry_price=120.0,
            initial_stop_loss=150.0,
            breakeven_enabled=True,
            trailing_sl_enabled=True,
            trailing_distance_pips=10.0
        )
        self.assertIsNotNone(trade.id)
        self.assertEqual(trade.status, "OPEN")
        self.assertEqual(trade.current_stop_loss, 150.0)

        # Record SL modification
        sl_mod = TradeRepository.record_sl_modification(
            order_id="ORD-1001",
            old_sl=150.0,
            new_sl=120.0,
            current_price=105.0,
            reason="BREAKEVEN"
        )
        self.assertIsNotNone(sl_mod.id)
        self.assertEqual(sl_mod.new_sl, 120.0)

        # Close trade
        closed = TradeRepository.close_trade(
            order_id="ORD-1001",
            exit_price=90.0,
            pnl=1500.0,
            reason="TARGET_HIT"
        )
        self.assertIsNotNone(closed)
        self.assertEqual(closed.status, "CLOSED")
        self.assertEqual(closed.pnl, 1500.0)

    def test_audit_log(self):
        log = TradeRepository.record_audit(
            event_type="KILL_SWITCH",
            severity="CRITICAL",
            message="Daily loss threshold exceeded",
            context={"daily_loss": -2500.0, "threshold": -2000.0}
        )
        self.assertIsNotNone(log.id)
        self.assertEqual(log.event_type, "KILL_SWITCH")
        self.assertEqual(log.severity, "CRITICAL")


if __name__ == "__main__":
    unittest.main()
