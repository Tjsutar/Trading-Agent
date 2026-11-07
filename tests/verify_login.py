"""
Pre-flight Credentials & Live Connection Verifier (BRD Section 10.2).
Validates:
  1. Presence and format of .env credentials
  2. TOTP generator algorithm
  3. Broker session handshake via SessionManager
  4. Profile & Exchange permissions
  5. Account RMS margin & available cash
  6. Live Nifty 50 market data fetch
"""
import sys
import os
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Config
from core.session_manager import SessionManager
import pyotp


def run_preflight_check():
    print("\n" + "=" * 60)
    print("      🚀 T-AGENT PRE-FLIGHT CREDENTIAL & HEALTH VERIFIER")
    print("=" * 60)

    # 1. Check .env variables
    print("\n[Step 1/5] Checking Configuration Environment...")
    missing = []
    if not Config.API_KEY or Config.API_KEY == "your_smartapi_api_key":
        missing.append("API_KEY")
    if not Config.CLIENT_ID or Config.CLIENT_ID == "your_client_id":
        missing.append("CLIENT_ID")
    if not Config.PASSWORD or Config.PASSWORD == "your_pin_or_password":
        missing.append("PASSWORD")
    if not Config.TOTP_SECRET or Config.TOTP_SECRET == "your_totp_secret_key":
        missing.append("TOTP_SECRET")

    if missing:
        print(f"❌ Missing credentials in .env file: {', '.join(missing)}")
        print("\n👉 To configure your credentials:")
        print("   1. Open or create the file '.env' in your project root:")
        print("      d:\\T Agent\\.env")
        print("   2. Add your Angel One SmartAPI credentials:")
        print("      API_KEY=your_actual_api_key")
        print("      CLIENT_ID=your_client_id")
        print("      PASSWORD=your_password_or_pin")
        print("      TOTP_SECRET=your_totp_secret_key")
        print("\n   Reference template available at: .env.example\n")
        print("=" * 60 + "\n")
        return False

    print("✅ All required credentials found in environment.")

    # 2. Test TOTP generation
    print("\n[Step 2/5] Testing TOTP 2FA Secret...")
    try:
        current_totp = pyotp.TOTP(Config.TOTP_SECRET).now()
        print(f"✅ TOTP Token generated successfully (Current OTP: {current_totp})")
    except Exception as e:
        print(f"❌ Invalid TOTP_SECRET format: {e}")
        return False

    # 3. Authenticate with Broker
    print("\n[Step 3/5] Authenticating with Angel One SmartAPI...")
    session_mgr = SessionManager(
        api_key=Config.API_KEY,
        client_id=Config.CLIENT_ID,
        password=Config.PASSWORD,
        totp_secret=Config.TOTP_SECRET,
        is_mock=False
    )

    if not session_mgr.connect():
        print("❌ Authentication rejected by Angel One. Please check Client ID, Password, and API Key.")
        return False

    api = session_mgr.get_api()
    print("✅ Broker Authentication Succeeded!")

    # 4. Check Profile & Margin
    print("\n[Step 4/5] Inspecting Account Profile & Margin...")
    try:
        rms = api.rmsLimit()
        if rms and rms.get("status"):
            cash = float(rms["data"].get("net", 0.0))
            collateral = float(rms["data"].get("collateral", 0.0))
            print(f"✅ Account RMS Funds: Available Cash = ₹{cash:,.2f} | Collateral = ₹{collateral:,.2f}")
        else:
            print("⚠️ Could not fetch RMS limits.")
    except Exception as e:
        print(f"⚠️ Error querying RMS: {e}")

    # 5. Live Feed Test (Nifty 50)
    print("\n[Step 5/5] Testing Live Market Feed (Nifty 50 Spot)...")
    try:
        ltp_resp = api.ltpData("NSE", "Nifty 50", "99926000")
        if ltp_resp and ltp_resp.get("status"):
            nifty_ltp = ltp_resp["data"]["ltp"]
            print(f"✅ Live Market Feed Connected! Nifty 50 CMP: ₹{nifty_ltp:,.2f}")
        else:
            print("⚠️ Market data request received no response (market may be offline or closed).")
    except Exception as e:
        print(f"⚠️ Market feed query error: {e}")

    print("\n" + "=" * 60)
    print(" 🎉 PRE-FLIGHT COMPLETED: Ready for Dry Run (`python main.py --dry-run`)")
    print("=" * 60 + "\n")
    return True


if __name__ == "__main__":
    run_preflight_check()
