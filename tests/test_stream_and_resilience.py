"""
Unit tests for ResilientSmartClient and MarketDataStream (WebSocket streaming engine).
"""
import unittest
import time
from datetime import datetime
import pandas as pd

from core.resilient_client import ResilientSmartClient
from core.market_data_stream import MarketDataStream, CandleSynthesizer
from utils.rate_limiter import TokenBucketRateLimiter


class MockSmartConnectWithThrottle:
    """Simulates Angel One API with transient rate limits."""
    def __init__(self):
        self.call_count = 0
        self.throttle_until = 2

    def getLtpData(self, exchange, symbol, token):
        self.call_count += 1
        if self.call_count <= self.throttle_until:
            return {"status": False, "message": "Access denied because of exceeding access rate", "errorcode": "AB1004"}
        return {"status": True, "data": {"ltp": 25100.50}}

    def getCandleData(self, params):
        return {"status": True, "data": [["2026-11-08 15:25", 25000, 25020, 24990, 25010, 1500]]}


class TestStreamAndResilience(unittest.TestCase):

    def test_resilient_client_throttle_recovery(self):
        raw_mock = MockSmartConnectWithThrottle()
        client = ResilientSmartClient(raw_mock)

        # Call should automatically retry on AB1004 and succeed on 3rd attempt
        res = client.getLtpData("NSE", "Nifty 50", "99926000")
        self.assertTrue(res.get("status"))
        self.assertEqual(res["data"]["ltp"], 25100.50)
        self.assertEqual(raw_mock.call_count, 3)

    def test_resilient_client_ltp_caching(self):
        raw_mock = MockSmartConnectWithThrottle()
        raw_mock.throttle_until = 0  # No throttles
        client = ResilientSmartClient(raw_mock)

        # 1st call hits mock
        res1 = client.getLtpData("NSE", "Nifty 50", "99926000")
        self.assertTrue(res1.get("status"))
        self.assertEqual(raw_mock.call_count, 1)

        # 2nd immediate call (within 500ms) should be served from memory cache
        res2 = client.getLtpData("NSE", "Nifty 50", "99926000")
        self.assertTrue(res2.get("status"))
        self.assertTrue(res2.get("cached"))
        self.assertEqual(raw_mock.call_count, 1)  # Did not hit API again!

    def test_candle_synthesizer(self):
        synthesizer = CandleSynthesizer(interval_minutes=5)
        now = datetime(2026, 11, 8, 9, 15, 10)

        # Feed ticks into 09:15 bucket
        synthesizer.update_tick(now, price=25000.0, volume=10)
        synthesizer.update_tick(datetime(2026, 11, 8, 9, 16, 0), price=25050.0, volume=20)
        synthesizer.update_tick(datetime(2026, 11, 8, 9, 17, 0), price=24980.0, volume=15)
        synthesizer.update_tick(datetime(2026, 11, 8, 9, 19, 50), price=25030.0, volume=5)

        # Check current active candle
        df = synthesizer.get_dataframe()
        self.assertEqual(len(df), 1)
        candle = df.iloc[0]
        self.assertEqual(candle["open"], 25000.0)
        self.assertEqual(candle["high"], 25050.0)
        self.assertEqual(candle["low"], 24980.0)
        self.assertEqual(candle["close"], 25030.0)
        self.assertEqual(candle["volume"], 50.0)

        # Roll over into 09:20 bucket
        synthesizer.update_tick(datetime(2026, 11, 8, 9, 20, 1), price=25040.0, volume=10)
        df2 = synthesizer.get_dataframe()
        self.assertEqual(len(df2), 2)  # Previous candle completed, new one active!

    def test_market_data_stream_mock_mode(self):
        stream = MarketDataStream(is_mock=True)
        self.assertTrue(stream.start())
        stream.subscribe("99926000", exchange_type=1)

        # Inject simulated tick
        stream._live_ltp["99926000"] = 24995.0
        stream._last_tick_time["99926000"] = datetime.now()

        # In-memory retrieval in 0ms
        ltp = stream.get_ltp("99926000", exchange_type=1)
        self.assertEqual(ltp, 24995.0)
        stream.stop()


if __name__ == "__main__":
    unittest.main()
