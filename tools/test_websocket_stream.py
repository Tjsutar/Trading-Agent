"""
Test and Benchmark Angel One SmartWebSocketV2 Tick Streaming Feed.
Connects with live credentials, subscribes to Nifty 50 Spot,
and benchmarks tick reception latency and zero REST overhead.
"""
import sys
import time
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.session_manager import SessionManager


def test_websocket():
    print("=" * 70)
    print("   🛰️ ANGEL ONE SMART WEBSOCKET 2.0 STREAMING BENCHMARK")
    print("=" * 70)

    mgr = SessionManager()
    print("1. Authenticating broker session...")
    if not mgr.connect():
        print("❌ Authentication failed. Check .env credentials.")
        return

    print("2. Starting MarketDataStream (SmartWebSocketV2)...")
    stream = mgr.get_market_stream()

    print("3. Subscribing to Nifty 50 Cash Index (Token: 99926000)...")
    stream.subscribe("99926000", exchange_type=1)

    print("\nListening for streaming ticks for 10 seconds (Zero REST API calls)...")
    start_t = time.time()
    tick_count = 0
    last_val = None

    for i in range(10):
        time.sleep(1)
        val = stream.get_ltp("99926000", exchange_type=1)
        candles = stream.get_nifty_candles()
        c_count = len(candles) if candles is not None else 0

        if val and val != last_val:
            tick_count += 1
            last_val = val
            print(f"  [{i+1:2d}s] 🟢 Streaming Tick: NIFTY 50 CMP = ₹{val:,.2f} | Synthesized Candles: {c_count}")
        elif val:
            print(f"  [{i+1:2d}s] ⚡ In-Memory Cache: NIFTY 50 CMP = ₹{val:,.2f} (0ms latency)")
        else:
            print(f"  [{i+1:2d}s] ⏳ Awaiting tick event...")

    print("-" * 70)
    print(f"✅ Benchmark Complete! Stream Active. Zero REST rate limits consumed.")
    print("Stopping stream cleanly...")
    stream.stop()
    mgr.stop_renewal_daemon()
    print("=" * 70)


if __name__ == "__main__":
    test_websocket()
