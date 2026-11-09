import argparse
import sys
from core.session_manager import SessionManager
from core.angel_connect import get_angel_session
from utils.token_lookup import TokenLookup
from strategies.nifty_straddle import NiftyStrategy
from core.mock_connect import MockSmartConnect, MockTokenLookup
from utils.expiry_calculator import get_next_weekly_expiry
from strategies.orb_strategy import ORBStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.vwap_strategy import VWAPStrategy
from strategies.ohl_strategy import OHLStrategy
from strategies.inside_bar_strategy import InsideBarStrategy
from strategies.smc_liquidity_strategy import SMCLiquidityStrategy
from core.paper_broker import PaperBroker
from core.decision_engine import DecisionEngine
from db.database import init_db
from db.repository import TradeRepository

def run_bot():
    parser = argparse.ArgumentParser(description="Nifty Options Trading Bot")
    parser.add_argument("--test", action="store_true", help="Run in Mock Mode for local testing")
    parser.add_argument("--paper", action="store_true", help="Run in Paper Trading Mode (Realistic Simulated Execution)")
    parser.add_argument("--dry-run", action="store_true", help="Run with Real Data but DO NOT place orders")
    parser.add_argument("--strategy", type=str, default="STRADDLE", choices=["STRADDLE", "ORB", "MOMENTUM", "VWAP", "OHL", "INSIDE_BAR", "SMC"], help="Choose Strategy")
    parser.add_argument("--auto", action="store_true", help="Enable Smart Auto-Mode (AI Selects Strategy)")
    args = parser.parse_args()

    # Ensure database schema is initialized
    init_db()

    session_mgr = None
    if args.test:
        print("\n>>> [System] STARTING IN MOCK MODE 🟢")
        mock_api = MockSmartConnect()
        session_mgr = SessionManager(is_mock=True, mock_instance=mock_api)
        session_mgr.connect()
        api = session_mgr.get_api()
        loader = MockTokenLookup()
        loader.load_scrip_master()
    elif args.paper:
        print("\n>>> [System] STARTING IN PAPER TRADING MODE 📄")
        print("    (Realistic Simulated Execution with ₹15,000 Capital & Database Persistence)")
        args.dry_run = True  # Guarantee ZERO real orders sent to broker

        # Check if Angel One credentials exist in environment to provide LIVE market data stream
        has_creds = all([Config.API_KEY, Config.CLIENT_ID, Config.PASSWORD, Config.TOTP_SECRET])
        live_connected = False
        if has_creds:
            print(">>> [Paper] Connecting to Angel One Live Market Stream for authentic live ticks...")
            session_mgr = SessionManager(is_mock=False)
            if session_mgr.connect():
                session_mgr.start_renewal_daemon(interval_seconds=60)
                api = session_mgr.get_api()
                loader = TokenLookup()
                loader.load_scrip_master()
                live_connected = True
                print("✅ [Paper] Authenticated with Angel One. Live ticks & Option chain connected in Paper Mode (0 real risk).")
            else:
                print("⚠️ [Paper] Broker login failed. Falling back to offline PaperBroker.")

        if not live_connected:
            paper_api = PaperBroker(starting_cash=15000.0)
            session_mgr = SessionManager(is_mock=True, mock_instance=paper_api)
            session_mgr.connect()
            api = session_mgr.get_api()
            loader = MockTokenLookup()
            loader.load_scrip_master()
    else:
        # 1. Initialize Connection via SessionManager
        if args.dry_run:
            print("\n>>> [System] STARTING IN DRY RUN MODE 🟡") 
            print("    (Real Data, No Orders)")
        
        session_mgr = SessionManager(is_mock=False)
        if not session_mgr.connect():
            print("\n❌ [System] Authentication Failed: Broker credentials not configured or rejected.")
            print("👉 Run 'python tests/verify_login.py' to diagnose your .env credentials.")
            return

        session_mgr.start_renewal_daemon(interval_seconds=60)
        api = session_mgr.get_api()

        # 2. Initialize Data Loader
        loader = TokenLookup()
        loader.load_scrip_master()

    # 3. Smart Auto-Selection (The Brain)
    if args.auto:
        print("\n>>> [System] 🧠 SMART AUTO-MODE ACTIVATED")
        engine = DecisionEngine(api)
        selected_strategy = engine.analyze_and_select()
        
        if selected_strategy:
            print(f">>> [Auto] 🤖 Brain selected: {selected_strategy}")
            args.strategy = selected_strategy
        else:
            print(">>> [Auto] ❌ Brain could not select a strategy (Low Funds or Market Closed). Exiting.")
            return

    # 4. Initialize Strategy Strategies logic...
    # In test mode, api and loader are mocks. Strategy should work transparently.
    # In dry_run mode, we pass True to dry_run arg of Strategy
    
    market_stream = session_mgr.get_market_stream() if hasattr(session_mgr, "get_market_stream") else None

    if args.strategy == "ORB":
        print(f"\n>>> [Strategy] Selected: Open Range Breakout (ORB)")
        bot = ORBStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "MOMENTUM":
        print(f"\n>>> [Strategy] Selected: Momentum (EMA Crossover) ⚡")
        bot = MomentumStrategy(api, loader, dry_run=args.dry_run, starting_capital=15000.0, market_stream=market_stream)
    elif args.strategy == "VWAP":
        print(f"\n>>> [Strategy] Selected: VWAP Institutional Trend (Pro Mode) 🚀")
        bot = VWAPStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "OHL":
        print(f"\n>>> [Strategy] Selected: Open High Low (OHL) Scalp 🎯")
        bot = OHLStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "INSIDE_BAR":
        print(f"\n>>> [Strategy] Selected: Inside Bar Breakout 🔥")
        bot = InsideBarStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "SMC":
        print(f"\n>>> [Strategy] Selected: Institutional SMC Liquidity 🏛️")
        bot = SMCLiquidityStrategy(api, loader, dry_run=args.dry_run)
    else:
        print(f"\n>>> [Strategy] Selected: 9:20 Straddle (Short) 📉")
        bot = NiftyStrategy(api, loader, dry_run=args.dry_run)

    # 4. Input Trade Parameters
    print("\n--- NIFTY OPTION TRADER ---")
    
    # Calculate Dynamic Weekly Expiry (Next Tuesday)
    expiry = get_next_weekly_expiry()
    print(f">>> [Setup] Target Expiry: {expiry}")
    
    # 5. Execute Strategy
    # WARNING: This places a REAL order if credentials are valid (and not in test mode).
    # Strike is now calculated dynamically (ATM)
    
    if args.strategy in ["ORB", "OHL", "INSIDE_BAR"]:
        # Directional buying
        bot.execute(expiry=expiry, action="BUY")
    elif args.strategy == "MOMENTUM":
        bot.execute(expiry=expiry)
    elif args.strategy == "SMC":
        bot.execute(expiry=expiry)
    else:
        # Straddle (Short)
        bot.execute(expiry=expiry, action="SELL")

if __name__ == "__main__":
    run_bot()
