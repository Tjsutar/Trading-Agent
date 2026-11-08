"""
Download and cache 3 months of real historical M5 candles from Angel One SmartAPI.
"""
import sys
import os
import time
from datetime import datetime, timedelta
import pandas as pd
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.session_manager import SessionManager


def download_3m_data(output_file: str = "data/nifty_3m_5min.csv"):
    mgr = SessionManager()
    if not mgr.connect():
        print("Failed to connect to Angel One.")
        return

    api = mgr.get_api()
    all_candles = []
    now = datetime.now()

    print("Fetching 3 months of authentic Nifty 50 historical candles...")
    for chunk in range(3):
        time.sleep(1.2)  # Respect Angel One rate limit
        chunk_to = now - timedelta(days=chunk * 30)
        chunk_from = now - timedelta(days=(chunk + 1) * 30)

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
            print(f"  • Chunk {chunk + 1} ({chunk_from.strftime('%Y-%m-%d')} to {chunk_to.strftime('%Y-%m-%d')}): {len(rows)} candles")
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
            print(f"  • Chunk {chunk + 1} error:", res)

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
    download_3m_data()
