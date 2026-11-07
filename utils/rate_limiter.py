"""
Token Bucket Rate Limiter to eliminate blocking sleep calls while respecting broker API limits.
Complies with BRD latency requirements (<100ms floor).
"""
import time
import threading
from typing import Optional


class TokenBucketRateLimiter:
    """
    Thread-safe Token Bucket Rate Limiter.
    Allows bursts up to `capacity`, refilling at `rate` tokens per second.
    """

    def __init__(self, rate: float = 3.0, capacity: float = 10.0):
        """
        :param rate: Number of tokens added per second. (e.g. 3 requests/sec)
        :param capacity: Maximum number of accumulated tokens (burst capacity).
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def acquire(self, tokens: float = 1.0, timeout: Optional[float] = None) -> bool:
        """
        Acquire tokens, waiting only if necessary.
        Returns True if acquired within timeout, False otherwise.
        """
        start_time = time.monotonic()
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                
                # Calculate minimum wait time
                needed = tokens - self.tokens
                wait_time = needed / self.rate

            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed + wait_time > timeout:
                    return False

            # Wait only for the micro-duration needed rather than hardcoded 0.5s/1.0s
            time.sleep(min(wait_time, 0.05))

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking attempt to acquire tokens."""
        with self._lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


# Global rate limiter instances for standard broker endpoints
market_data_limiter = TokenBucketRateLimiter(rate=5.0, capacity=10.0)  # 5 req/s
order_execution_limiter = TokenBucketRateLimiter(rate=10.0, capacity=20.0)  # 10 req/s
