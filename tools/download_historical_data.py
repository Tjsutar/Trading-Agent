"""
Download and cache historical M5 candles from Angel One SmartAPI.
Supports 1 month, 3 months, 6 months, and 1 year data retrieval.
"""
import sys
import os
import time
from datetime import datetime, timedelta
import pandas as pd
from pathlib import Path
import argparse

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.session_manager import SessionManager


def download_historical_data(days: int = 365, output_file: str = "data/nifty_1y_5min.csv") -> pd.DataFrame:
    mgr = SessionManager()
    if not mgr.connect():
        print("❌ Failed to connect to Angel One SmartAPI.")
        return None

    api = mgr.get_api()
    all_candles = []
    now = datetime.now()
    num_chunks = (days // 30) + (1 if days % 30 != 0 else 0)

    print(f"📥 Fetching {days} days (~{num_chunks} months) of authentic Nifty 50 historical 5-min candles...")
    for chunk in range(num_chunks):
        time.sleep(1.2)  # Respect Angel One rate limit
        chunk_to = now - timedelta(days=chunk * 30)
        chunk_from = now - timedelta(days=min((chunk + 1) * 30, days))

        params = {
            "exchange": "NSE",
            "symboltoken": "99926000",
            "interval": "FIVE_MINUTE",
            "fromdate": chunk_from.strftime("%Y-%m-%d %H:%M"),
            "todate": chunk_to.strftime("%Y-%m-%d %H:%M")
        }
        res = api.getCandleData(params)
        if res and res.get("status") and res.get("data"):
            rows = res["data"]
            print(f"  • Chunk {chunk + 1}/{num_chunks} ({chunk_from.strftime('%Y-%m-%d')} to {chunk_to.strftime('%Y-%m-%d')}): {len(rows)} candles")
            for r in rows:
                all_candles.append({
                    "time": pd.to_datetime(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]) if len(r) > 5 else 1000.0
                })
        else:
            print(f"  • Chunk {chunk + 1}/{num_chunks} error:", res)

    if not all_candles:
        print("❌ No candle data retrieved.")
        return None

    df = pd.DataFrame(all_candles)
    df.drop_duplicates(subset=["time"], inplace=True)
    df.sort_values(by="time", inplace=True)
    df.set_index("time", inplace=True)

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path)
    print(f"\n✅ Successfully saved {len(df)} authentic historical candles to: {out_path.resolve()}\n")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Angel One Nifty 50 historical candles")
    parser.add_argument("--days", type=int, default=365, help="Number of days to download (default: 365)")
    parser.add_argument("--output", type=str, default="data/nifty_1y_5min.csv", help="Output CSV path")
    args = parser.parse_args()

    download_historical_data(days=args.days, output_file=args.output)
