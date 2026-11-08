"""
Resilient SmartAPI Client Proxy (BRD FR-EX-003).
Protects against broker rate limits, cascading throttles, and connection drops.
Features:
  1. Token Bucket Rate Limiting (1 req/s for Market Data, 5 req/s for Orders)
  2. Auto-Retry with Exponential Backoff and Jitter on AB1004 / 429
  3. Automatic Session Reconnect on token expiry (AB1010 / AG8001)
  4. Micro-caching (500ms TTL) for duplicate quote calls
  5. Audit logging of throttles and recovery events
"""
import time
import random
import threading
from typing import Any, Dict, Optional, Callable
from utils.rate_limiter import TokenBucketRateLimiter
from utils.logger import get_logger
from db.repository import TradeRepository

logger = get_logger("ResilientClient")

# Dedicated rate limiters matching Angel One's true broker thresholds
market_data_limiter = TokenBucketRateLimiter(rate=1.2, capacity=3.0)  # ~1.2 req/sec
order_execution_limiter = TokenBucketRateLimiter(rate=5.0, capacity=10.0)  # 5 req/sec


class ResilientSmartClient:
    """
    Transparent proxy wrapping SmartConnect that prevents rate-limit lockouts
    and auto-recovers from transient network/broker errors.
    """

    def __init__(self, raw_api: Any, session_manager: Optional[Any] = None):
        self._raw_api = raw_api
        self._session_manager = session_manager
        self._ltp_cache: Dict[str, Tuple[float, float]] = {}  # token -> (price, timestamp)
        self._cache_lock = threading.Lock()
        self._cache_ttl = 0.5  # 500ms TTL

    def __getattr__(self, name: str) -> Any:
        """
        Intercepts API methods to inject rate limiting and backoff recovery.
        Non-intercepted attributes pass directly to the underlying SmartConnect object.
        """
        attr = getattr(self._raw_api, name)
        if callable(attr):
            if name in ["getCandleData", "getLtpData", "getMarketData"]:
                return self._wrap_market_data(attr, name)
            elif name in ["placeOrder", "modifyOrder", "cancelOrder"]:
                return self._wrap_order_execution(attr, name)
        return attr

    def _execute_with_backoff(
        self,
        func: Callable,
        func_name: str,
        limiter: TokenBucketRateLimiter,
        args: tuple,
        kwargs: dict,
        max_retries: int = 3
    ) -> Any:
        for attempt in range(1, max_retries + 1):
            # 1. Enforce Token Bucket Rate Limit Floor
            limiter.acquire(1.0)

            try:
                result = func(*args, **kwargs)

                # Check if broker returned a throttle error code
                if isinstance(result, dict) and not result.get("status"):
                    err_code = str(result.get("errorcode", ""))
                    msg = str(result.get("message", ""))

                    # Rate Limit Exceeded: AB1004 or "exceeding access rate"
                    if "AB1004" in err_code or "exceeding access rate" in msg.lower() or "too many requests" in msg.lower():
                        jitter = random.uniform(0.3, 0.8)
                        delay = min(8.0, (1.5 ** attempt) + jitter)
                        logger.warning(
                            f"⚠️ Angel One Rate Limit (AB1004) on {func_name}()! Backing off for {delay:.2f}s (Attempt {attempt}/{max_retries})..."
                        )
                        TradeRepository.record_audit(
                            event_type="RATE_LIMIT_BACKOFF",
                            severity="WARNING",
                            message=f"Rate limit throttle on {func_name}. Backing off {delay:.2f}s.",
                            context={"attempt": attempt, "func": func_name, "error": msg}
                        )
                        time.sleep(delay)
                        continue

                    # Session Expired / Invalid Token: AB1010, AG8001
                    if any(c in err_code for c in ["AB1010", "AG8001"]) or "invalid token" in msg.lower():
                        logger.warning(f"🔄 Token expired during {func_name}(). Requesting session reconnect...")
                        if self._session_manager and self._session_manager.reconnect():
                            self._raw_api = self._session_manager.api
                            func = getattr(self._raw_api, func_name)
                            continue

                return result

            except Exception as e:
                err_str = str(e).lower()
                if "rate limit" in err_str or "429" in err_str:
                    delay = min(8.0, (2.0 ** attempt) + random.uniform(0.2, 0.6))
                    logger.warning(f"⚠️ Exception 429 on {func_name}: {e}. Retrying after {delay:.2f}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"❌ Exception in {func_name}: {e}")
                    raise e

        # If all retries exhausted, log critical alert
        logger.critical(f"🛑 ResilientClient: All {max_retries} retries exhausted for {func_name}()!")
        return {"status": False, "message": f"Rate limit / throttle exhausted after {max_retries} retries", "errorcode": "EXHAUSTED"}

    def _extract_token(self, args: tuple) -> str:
        if not args:
            return ""
        if isinstance(args[0], dict):
            return str(args[0].get("symboltoken", ""))
        if len(args) >= 3:
            return str(args[2])
        return ""

    def _wrap_market_data(self, func: Callable, name: str) -> Callable:
        def wrapper(*args, **kwargs):
            # Fast-path for getLtpData caching
            if name in ["getLtpData", "ltpData"]:
                token = self._extract_token(args)
                if token:
                    now = time.monotonic()
                    with self._cache_lock:
                        if token in self._ltp_cache:
                            val, ts = self._ltp_cache[token]
                            if now - ts < self._cache_ttl:
                                return {"status": True, "data": {"ltp": val}, "cached": True}

            res = self._execute_with_backoff(func, name, market_data_limiter, args, kwargs)

            # Update cache if successful getLtpData
            if name in ["getLtpData", "ltpData"] and isinstance(res, dict) and res.get("status") and res.get("data"):
                ltp_val = res["data"].get("ltp")
                if ltp_val is not None:
                    tok = self._extract_token(args)
                    if tok:
                        with self._cache_lock:
                            self._ltp_cache[tok] = (float(ltp_val), time.monotonic())

            return res
        return wrapper

    def _wrap_order_execution(self, func: Callable, name: str) -> Callable:
        def wrapper(*args, **kwargs):
            return self._execute_with_backoff(func, name, order_execution_limiter, args, kwargs)
        return wrapper

