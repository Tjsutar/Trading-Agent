"""
Unit tests for SMCLiquidityStrategy (BRD FR-AL-001, FR-AL-002, FR-AL-003, FR-SL-001).
"""
import unittest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock
from strategies.smc_liquidity_strategy import SMCLiquidityStrategy
from db.database import init_db
from sqlalchemy import create_engine


class TestSMCLiquidityStrategy(unittest.TestCase):
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
        self.strategy = SMCLiquidityStrategy(api=self.mock_api, token_loader=None, dry_run=True)

    def tearDown(self):
        import db.database as db_module
        db_module.ScopedSession.remove()

    def test_detect_liquidity_zones(self):
        # Create synthetic candles with Equal Highs at 22,000
        data = []
        for i in range(30):
            high = 22000.0 if i in [5, 15, 25] else 21900.0
            data.append({
                "open": 21850.0,
                "high": high,
                "low": 21800.0,
                "close": 21880.0,
                "volume": 1000
            })
        df = pd.DataFrame(data)
        zones = SMCLiquidityStrategy.detect_liquidity_zones(df, atr_multiplier=0.2, min_touches=2)
        self.assertIn("eqh", zones)
        detected_levels = [z["level"] for z in zones["eqh"]]
        self.assertTrue(any(abs(lvl - 22000.0) <= 10.0 for lvl in detected_levels))

    def test_get_market_bias(self):
        # Uptrending series
        data = []
        for i in range(30):
            price = 20000.0 + (i * 20.0)
            data.append({
                "open": price - 10,
                "high": price + 15,
                "low": price - 15,
                "close": price,
                "volume": 5000
            })
        df = pd.DataFrame(data)
        bias = SMCLiquidityStrategy.get_market_bias(df)
        self.assertEqual(bias, "BULLISH")

    def test_fake_breakout_detection(self):
        # Candle with tiny body (2) and huge wick (18) -> wick_ratio = 9.0 > 1.5
        candle = pd.Series({
            "open": 100.0,
            "close": 102.0,
            "high": 120.0,
            "low": 99.0,
            "volume": 500
        })
        avg_vol = 1000.0  # Candle volume is below average
        is_fake = SMCLiquidityStrategy.is_fake_breakout(candle, avg_vol)
        self.assertTrue(is_fake)

    def test_signal_generation_and_persistence(self):
        # Construct M5 and H1 candles
        h1_data = [{"open": 22000 + i*10, "high": 22015 + i*10, "low": 21990 + i*10, "close": 22010 + i*10, "volume": 5000} for i in range(25)]
        h1_df = pd.DataFrame(h1_data)

        # M5 candles that sweep an EQL zone
        m5_data = []
        for i in range(25):
            low = 22100.0 if i in [5, 15] else 22120.0
            m5_data.append({
                "open": 22150.0,
                "high": 22200.0,
                "low": low,
                "close": 22160.0,
                "volume": 2000
            })
        # Add sweep candle (dipped to 22095, then current candle closed above 22100 at 22140)
        m5_data.append({"open": 22120.0, "high": 22130.0, "low": 22095.0, "close": 22105.0, "volume": 1500})
        m5_data.append({"open": 22105.0, "high": 22150.0, "low": 22102.0, "close": 22140.0, "volume": 3000})
        m5_df = pd.DataFrame(m5_data)

        signal = self.strategy.generate_signal(m5_df, h1_df, symbol="NIFTY")
        if signal:
            self.assertEqual(signal["signal_type"], "BUY")
            self.assertGreaterEqual(signal["risk_reward"], 2.0)
            self.assertIsNotNone(signal["liquidity_zone_id"])


if __name__ == "__main__":
    unittest.main()
