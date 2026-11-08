import os
from dotenv import load_dotenv

# Load secrets from .env file
load_dotenv()

class Config:
    API_KEY = os.getenv("API_KEY")
    CLIENT_ID = os.getenv("CLIENT_ID")
    PASSWORD = os.getenv("PASSWORD")
    TOTP_SECRET = os.getenv("TOTP_SECRET")
    
    # Nifty Constants (Updated for 2026 NSE specification)
    NIFTY_LOT_SIZE = int(os.getenv("NIFTY_LOT_SIZE", "65"))
    # URL to fetch token IDs for all stocks
    SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

    # Anti-Loss & Trailing Stop Loss Configuration (Empirically Optimized)
    MIN_ADX_TREND = float(os.getenv("MIN_ADX_TREND", "22.0"))
    FAST_BREAKEVEN_PTS = float(os.getenv("FAST_BREAKEVEN_PTS", "10.0"))
    BREAKEVEN_LOCK_PTS = float(os.getenv("BREAKEVEN_LOCK_PTS", "2.0"))
    TSL_DISTANCE = float(os.getenv("TSL_DISTANCE", "8.0"))  # 8 pts trailing distance
    STOP_ON_FIRST_LOSS = os.getenv("STOP_ON_FIRST_LOSS", "true").lower() == "true"
    MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "1"))
    HARD_DAILY_STOP = float(os.getenv("HARD_DAILY_STOP", "-700.0"))
    SL_PTS = float(os.getenv("SL_PTS", "16.0"))
    TP_PTS = float(os.getenv("TP_PTS", "36.0"))

    # External Push Notifications (BRD FR-MR-001)
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
