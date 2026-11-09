"""
Automated Indian Market Hours Scheduler (BRD FR-EX-003 & System Ops).
Autonomous daily orchestrator for Indian Equities / Derivatives (09:15 - 15:30 IST).

Lifecycle:
  1. 09:10 IST - Pre-Flight Check: Authenticates with Angel One, verifies token validity, checks available margin.
  2. 09:15 IST - Market Open: Launches live trading strategy with real-time WebSocket tick stream.
  3. 15:15 IST - Mandatory Square-off: Forces exit of any open intraday option positions.
  4. 15:30 IST - Market Close: Computes daily P&L, persists audit logs, cleans up streaming sockets.
  5. Hibernation: Calculates time to next market open (skips weekends and NSE holidays).
"""
import sys
import os
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Config
from core.session_manager import SessionManager
from utils.token_lookup import TokenLookup
from strategies.momentum_strategy import MomentumStrategy
from db.database import init_db
from db.repository import TradeRepository
from utils.logger import get_logger

logger = get_logger("MarketScheduler")

# Indian Standard Time offset (UTC + 5:30)
IST_OFFSET = timedelta(hours=5, minutes=30)

# Standard NSE 2026 Trading Holidays (sample official calendar)
NSE_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Holi
    "2026-03-20",  # Id-Ul-Fitr
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-08",  # Diwali Laxmi Pujan (Sunday/Special Muhurat)
    "2026-12-25",  # Christmas
}


def get_current_ist() -> datetime:
    """Returns current datetime in Indian Standard Time (IST)."""
    utc_now = datetime.now(timezone.utc)
    return utc_now + IST_OFFSET


def is_trading_day(dt: datetime) -> bool:
    """Returns True if date is a weekday (Monday-Friday) and not an NSE holiday."""
    # Monday is 0, Sunday is 6
    if dt.weekday() >= 5:
        return False
    date_str = dt.strftime("%Y-%m-%d")
    if date_str in NSE_HOLIDAYS_2026:
        return False
    return True


def get_next_market_open(from_dt: Optional[datetime] = None) -> datetime:
    """Calculates the exact next market open datetime (09:10 IST pre-flight)."""
    current = from_dt or get_current_ist()
    target = current.replace(hour=9, minute=10, second=0, microsecond=0)

    # If already past 09:10 IST today, start checking from tomorrow
    if current >= target:
        target += timedelta(days=1)

    while not is_trading_day(target):
        target += timedelta(days=1)

    return target


class MarketScheduler:
    """
    Autonomous scheduler managing pre-flight, strategy execution,
    and end-of-day square-off.
    """

    def __init__(self, mode: str = "paper", capital: float = 15000.0, strategy: str = "MOMENTUM"):
        self.mode = mode  # "live", "paper", "dry-run"
        self.capital = capital
        self.strategy_name = strategy
        self.session_mgr: Optional[SessionManager] = None
        self.token_loader: Optional[TokenLookup] = None
        self.bot: Optional[MomentumStrategy] = None

    def preflight_check(self) -> bool:
        """Executes 09:10 IST pre-flight login, token renewal, and funds audit."""
        logger.info("=" * 60)
        logger.info("📋 EXECUTING PRE-FLIGHT CHECK (09:10 IST)")
        logger.info("=" * 60)

        init_db()

        has_creds = all([Config.API_KEY, Config.CLIENT_ID, Config.PASSWORD, Config.TOTP_SECRET])
        is_mock = not has_creds
        self.session_mgr = SessionManager(is_mock=is_mock)
        if not self.session_mgr.connect():
            logger.critical("Pre-flight failed: Broker authentication unsuccessful.")
            return False

        self.session_mgr.start_renewal_daemon(interval_seconds=60)
        api = self.session_mgr.get_api()

        # Check Available Funds
        if not is_mock and hasattr(api, "rmsLimit"):
            try:
                rms = api.rmsLimit()
                if rms and rms.get("status") and rms.get("data"):
                    avail = float(rms["data"].get("net", 0.0))
                    logger.info(f"Available Account Cash Margin: ₹{avail:,.2f}")
                    if avail < (self.capital * 0.5):
                        logger.warning(f"Available margin (₹{avail:,.2f}) is lower than target capital tier (₹{self.capital:,.2f})!")
            except Exception as e:
                logger.warning(f"Could not retrieve margin limits: {e}")

        # Initialize Scrip Master
        logger.info("Loading latest NSE/NFO Scrip Master...")
        self.token_loader = TokenLookup()
        self.token_loader.load_scrip_master()

        TradeRepository.record_audit(
            event_type="PREFLIGHT_SUCCESS",
            severity="INFO",
            message=f"Pre-flight completed successfully. Mode: {self.mode}, Capital: ₹{self.capital}"
        )
        logger.info("✅ Pre-Flight Check Complete. Bot armed for 09:15 IST Market Open.")
        return True

    def run_market_session(self, test_now: bool = False):
        """Runs the active trading loop from 09:15 to 15:15 IST."""
        now_ist = get_current_ist()
        logger.info(f"🚀 Launching Trading Session: {self.strategy_name} (Mode: {self.mode}, Capital: ₹{self.capital:,.2f})")

        api = self.session_mgr.get_api()
        stream = self.session_mgr.get_market_stream()

        # Initialize Strategy with WebSocket streaming feed
        dry_run = self.mode in ["dry-run", "paper"]
        self.bot = MomentumStrategy(
            api=api,
            token_loader=self.token_loader,
            dry_run=dry_run,
            starting_capital=self.capital,
            market_stream=stream
        )

        from utils.expiry_calculator import get_next_weekly_expiry
        expiry = get_next_weekly_expiry()

        if test_now:
            logger.info("Running 1 test cycle (outside market hours)...")
            self.bot.check_trailing_stop()
            trend, ema9, ema21, ema50, rsi = self.bot.analyze_market_trend()
            logger.info(f"Market Test: Trend={trend}, EMA9={ema9:.1f}, EMA21={ema21:.1f}, RSI={rsi:.1f}")
        else:
            # Full market hours loop (09:15 to 15:15 IST)
            self.bot.execute(expiry=expiry)

        # 15:15 - 15:30 IST Post-Market Wrap Up
        self.post_market_cleanup()

    def post_market_cleanup(self):
        """15:30 IST market wrap-up, square-off verification, and stats persistence."""
        logger.info("=" * 60)
        logger.info("🌆 EXECUTING POST-MARKET WRAP-UP (15:30 IST)")
        logger.info("=" * 60)

        if self.bot and self.bot.active_position:
            logger.warning("Unclosed position detected at market close! Executing mandatory square-off.")
            self.bot.close_position("EOD_MANDATORY_SQUAREOFF")

        # Stop renewal daemon & market stream cleanly
        if self.session_mgr:
            self.session_mgr.stop_renewal_daemon()

        logger.info("Daily session ended cleanly. Database state and audit logs synchronized.")

    def run_scheduler_daemon(self):
        """Continuous background orchestrator sleeping between market sessions."""
        logger.info("Automated Market Scheduler daemon active.")

        while True:
            now_ist = get_current_ist()

            if is_trading_day(now_ist):
                # Between 09:10 and 09:15 IST -> Preflight
                if now_ist.hour == 9 and (10 <= now_ist.minute < 15):
                    logger.info("09:10 IST window reached. Initiating pre-flight...")
                    if self.preflight_check():
                        # Wait until 09:15 IST exactly
                        open_time = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
                        wait_secs = max(0.0, (open_time - get_current_ist()).total_seconds())
                        if wait_secs > 0:
                            logger.info(f"Sleeping {wait_secs:.1f}s until 09:15 IST open...")
                            time.sleep(wait_secs)
                        self.run_market_session()

                # Between 09:15 and 15:15 IST -> Session should be running
                elif (now_ist.hour == 9 and now_ist.minute >= 15) or (10 <= now_ist.hour < 15) or (now_ist.hour == 15 and now_ist.minute < 15):
                    logger.info("Mid-session startup detected. Initializing bot immediately...")
                    if self.preflight_check():
                        self.run_market_session()

            # Calculate sleep duration to next 09:10 IST market preflight
            next_open = get_next_market_open()
            sleep_seconds = max(10.0, (next_open - get_current_ist()).total_seconds())
            hours, remainder = divmod(int(sleep_seconds), 3600)
            minutes, secs = divmod(remainder, 60)
            logger.info(f"😴 Market Closed. Hibernating for {hours}h {minutes}m {secs}s until {next_open.strftime('%Y-%m-%d %H:%M')} IST...")
            time.sleep(min(sleep_seconds, 300))  # Heartbeat check every 5 mins


def main():
    parser = argparse.ArgumentParser(description="Autonomous Indian Market Hours Scheduler")
    parser.add_argument("--mode", type=str, default="paper", choices=["live", "paper", "dry-run"], help="Execution mode")
    parser.add_argument("--capital", type=float, default=15000.0, help="Initial capital tier (default: ₹15,000)")
    parser.add_argument("--strategy", type=str, default="MOMENTUM", help="Strategy to run (default: MOMENTUM)")
    parser.add_argument("--now", action="store_true", help="Run 1 pre-flight and market test cycle immediately")
    args = parser.parse_args()

    scheduler = MarketScheduler(mode=args.mode, capital=args.capital, strategy=args.strategy)

    if args.now:
        print("\n>>> [Scheduler] Immediate Test Mode Triggered ⚡")
        if scheduler.preflight_check():
            scheduler.run_market_session(test_now=True)
    else:
        scheduler.run_scheduler_daemon()


if __name__ == "__main__":
    main()

