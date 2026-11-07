"""
Unit tests for TokenBucketRateLimiter.
"""
import unittest
import time
from utils.rate_limiter import TokenBucketRateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_burst_capacity(self):
        # 5 tokens max, 2 per sec
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=5.0)
        self.assertTrue(limiter.try_acquire(5.0))
        self.assertFalse(limiter.try_acquire(1.0))

    def test_refill(self):
        limiter = TokenBucketRateLimiter(rate=10.0, capacity=5.0)
        self.assertTrue(limiter.try_acquire(5.0))
        time.sleep(0.2)  # Refills ~2 tokens
        self.assertTrue(limiter.acquire(1.0, timeout=0.5))


if __name__ == "__main__":
    unittest.main()

