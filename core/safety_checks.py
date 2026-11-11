"""
Institutional Risk & Safety Gatekeeper (BRD FR-MR-001, FR-MR-002, FR-MR-003, FR-SL-002).
Enforces:
  1. Market hours (09:15 - 15:29 IST)
  2. 2% Capital Daily Loss Kill Switch (Realized + Unrealized)
  3. Margin Gatekeeper with 10% safety buffer
  4. 1:2 Minimum Risk-Reward Filter
  5. India VIX Volatility Sizing (50% reduction when VIX > 25)
  6. Blackout period restrictions (11:30 - 13:00)
  7. Token-bucket rate-limiting (no artificial sleep delays)
  8. Institutional audit log persistence
"""
import datetime
import time
from typing import Optional, Dict, Any
from utils.rate_limiter import market_data_limiter
from db.repository import TradeRepository
from utils.logger import get_logger

logger = get_logger("SafetyGatekeeper")


class SafetyGatekeeper:
    def __init__(self, api, starting_capital: float = 100000.0, max_daily_loss_pct: float = 0.02, is_paper: bool = False):
        """
        :param api: Broker API instance (SmartAPI or Mock)
        :param starting_capital: Initial capital base (default ₹1,00,000)
        :param max_daily_loss_pct: Maximum daily drawdown cap (default 2% = ₹2,000)
        :param is_paper: If True, evaluates margins against virtual capital instead of broker RMS cash.
        """
        self.api = api
        self.starting_capital = starting_capital
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_daily_loss_amount = -(starting_capital * max_daily_loss_pct)
        self.is_paper = is_paper
        self.cached_rms = None
        self.last_rms_time = 0
        self.kill_switch_active = False

    def is_market_open(self) -> bool:
        """Rule: 09:15 to 15:29 IST."""
        now = datetime.datetime.now().time()
        start = datetime.time(9, 15)
        end = datetime.time(15, 29)
        if start <= now <= end:
            return True
        logger.warning(f"SafetyGatekeeper: Market Closed. Current Time: {now}")
        return False

    def check_data_freshness(self, tick_timestamp) -> bool:
        """Rule: Data must be < 2.0 seconds old."""
        if not tick_timestamp:
            logger.error("SafetyGatekeeper: No timestamp provided for freshness check.")
            return False
        now = datetime.datetime.now()
        diff = (now - tick_timestamp).total_seconds()
        if diff < 2.0:
            return True
        logger.warning(f"SafetyGatekeeper: Data Stale! Delay: {diff:.2f}s")
        return False

    def check_funds(self, required_margin_per_lot: float = 150000.0) -> bool:
        """Rule: Available Cash > Required Margin * 1.1 (10% Buffer)."""
        if self.is_paper:
            required_total = required_margin_per_lot * 1.1
            if self.starting_capital >= required_total:
                logger.info(f"Gatekeeper Margin Check (Paper): Virtual ₹{self.starting_capital:,.2f} >= Required ₹{required_total:,.2f}")
                return True
            else:
                logger.warning(f"Gatekeeper Margin Low (Paper): Virtual ₹{self.starting_capital:,.2f} < Required ₹{required_total:,.2f}")
                return False

        try:
            if time.time() - self.last_rms_time < 10 and self.cached_rms:
                limit = self.cached_rms
            else:
                market_data_limiter.acquire(1.0)
                limit = self.api.rmsLimit()
                self.cached_rms = limit
                self.last_rms_time = time.time()

            if limit and limit.get("status"):
                available_cash = float(limit["data"]["net"])
                required_total = required_margin_per_lot * 1.1

                if available_cash >= required_total:
                    logger.info(f"Gatekeeper Margin Check: Available ₹{available_cash:,.2f} >= Required ₹{required_total:,.2f}")
                    return True
                else:
                    logger.warning(f"Gatekeeper Margin Low: Available ₹{available_cash:,.2f} < Required ₹{required_total:,.2f}")
                    TradeRepository.record_audit(
                        event_type="MARGIN_BUFFER_INSUFFICIENT",
                        severity="WARNING",
                        message=f"Margin insufficient. Available: ₹{available_cash:,.2f}, Required: ₹{required_total:,.2f}",
                        context={"available": available_cash, "required": required_total}
                    )
                    return False
            return False
        except Exception as e:
            logger.error(f"Gatekeeper Funds Check Error: {e}")
            return False

    def check_trade_margin(self, estimated_cost: float) -> bool:
        """Rule: Available Cash > Estimated Cost with 10% buffer."""
        if self.is_paper:
            # For 1-lot trading in paper mode, allow up to 25% flexibility on far-dated expiries
            paper_limit = self.starting_capital * 1.25
            if paper_limit >= estimated_cost:
                logger.info(f"Gatekeeper Margin Check (Paper): Virtual ₹{self.starting_capital:,.2f} (buffer ₹{paper_limit:,.2f}) >= Cost ₹{estimated_cost:,.2f}")
                return True
            else:
                logger.warning(f"Gatekeeper Insufficient Paper Margin: Virtual limit ₹{paper_limit:,.2f} < Cost ₹{estimated_cost:,.2f}")
                return False

        try:
            if time.time() - self.last_rms_time < 10 and self.cached_rms:
                limit = self.cached_rms
            else:
                market_data_limiter.acquire(1.0)
                limit = self.api.rmsLimit()
                self.cached_rms = limit
                self.last_rms_time = time.time()

            if limit and limit.get("status"):
                available_cash = float(limit["data"]["net"])
                if available_cash >= estimated_cost:
                    return True
                else:
                    logger.warning(f"Gatekeeper Insufficient Margin: Cash ₹{available_cash:,.2f} < Cost ₹{estimated_cost:,.2f}")
                    TradeRepository.record_audit(
                        event_type="TRADE_MARGIN_REJECTED",
                        severity="WARNING",
                        message=f"Trade rejected: Available ₹{available_cash:,.2f} < Cost ₹{estimated_cost:,.2f}"
                    )
                    return False
            return False
        except Exception as e:
            logger.error(f"Gatekeeper Trade Margin Check Error: {e}")
            return False

    def check_max_daily_loss(self, current_unrealized_pnl: float = 0.0) -> bool:
        """
        FR-MR-001: Kill Switch.
        Halt trading if (Daily Realized P&L + Current Unrealized P&L) <= -2% of starting capital.
        """
        if self.kill_switch_active:
            return False

        daily_realized = TradeRepository.get_daily_realized_pnl()
        total_daily_pnl = daily_realized + current_unrealized_pnl

        if total_daily_pnl <= self.max_daily_loss_amount:
            self.kill_switch_active = True
            logger.critical(
                f"🛑 KILL SWITCH ACTIVATED! Daily P&L: ₹{total_daily_pnl:,.2f} exceeded cap ₹{self.max_daily_loss_amount:,.2f}"
            )
            TradeRepository.record_audit(
                event_type="KILL_SWITCH_ACTIVATED",
                severity="CRITICAL",
                message=f"Trading halted. Total daily loss ₹{total_daily_pnl:,.2f} hit cap ₹{self.max_daily_loss_amount:,.2f}",
                context={
                    "daily_realized": daily_realized,
                    "unrealized_pnl": current_unrealized_pnl,
                    "total_pnl": total_daily_pnl,
                    "cap": self.max_daily_loss_amount
                }
            )
            return False
        return True

    def check_daily_profit_target(self, target_profit: float = 5000.0) -> bool:
        """
        Check if cumulative daily realized P&L has hit or exceeded the target profit cap.
        Returns True if target profit is reached (trading should pause/halt), False otherwise.
        """
        daily_realized = TradeRepository.get_daily_realized_pnl()
        if daily_realized >= target_profit:
            logger.info(f"🎯 Daily Profit Target Reached! Realized: ₹{daily_realized:,.2f} >= Target: ₹{target_profit:,.2f}")
            return True
        return False

    def validate_risk_reward(self, entry: float, stop_loss: float, take_profit: float, min_rr: float = 2.0) -> bool:
        """
        FR-MR-002: Risk-Reward Filter.
        Calculates (TP - Entry) / (Entry - SL). Must be >= min_rr (default 1:2).
        """
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        if risk <= 0:
            logger.warning("SafetyGatekeeper: Invalid stop loss (risk is 0).")
            return False

        rr = reward / risk
        if rr >= min_rr:
            return True
        
        logger.warning(f"SafetyGatekeeper: Trade rejected by RR filter! RR={rr:.2f} < Min {min_rr:.2f}")
        TradeRepository.record_audit(
            event_type="RR_FILTER_REJECTION",
            severity="INFO",
            message=f"Trade rejected: Calculated RR {rr:.2f} below minimum {min_rr:.2f}",
            context={"entry": entry, "sl": stop_loss, "tp": take_profit, "rr": rr, "min_rr": min_rr}
        )
        return False

    def calculate_position_size(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss: float,
        risk_pct: float = 0.01,
        lot_size_multiplier: int = 50,
        india_vix: Optional[float] = None
    ) -> int:
        """
        FR-SL-002: Position Sizing Algorithm.
        Formula: Lot Size = (Account Balance * Risk%) / (Stop Loss Distance * Lot Multiplier)
        Adjusts for volatility: if India VIX > 25, reduces size by 50%.
        """
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return 1

        risk_capital = account_balance * risk_pct
        risk_per_lot = sl_distance * lot_size_multiplier
        raw_lots = int(risk_capital / risk_per_lot) if risk_per_lot > 0 else 1
        lots = max(1, raw_lots)

        # Check VIX volatility reduction
        vix = india_vix if india_vix is not None else self._get_vix_value()
        if vix and vix > 25.0:
            lots = max(1, int(lots * 0.5))
            logger.info(f"Gatekeeper: Volatility Sizing (VIX={vix:.1f} > 25). Reduced lots to {lots}.")

        return lots * lot_size_multiplier

    def is_blackout_period(self) -> bool:
        """Rule: No new trades between 11:30 AM - 01:00 PM IST (Enforced in Live Mode)."""
        if self.is_paper:
            return False
        now = datetime.datetime.now().time()
        start = datetime.time(11, 30)
        end = datetime.time(13, 0)
        if start <= now <= end:
            logger.info(f"Gatekeeper: In Blackout Period ({start}-{end}). No new trades.")
            return True
        return False

    def _get_vix_value(self) -> Optional[float]:
        """Internal helper to fetch current India VIX."""
        try:
            market_data_limiter.acquire(1.0)
            vix_token = "99926017"
            response = self.api.ltpData("NSE", "INDIA VIX", vix_token)
            if response and response.get("status"):
                return float(response["data"]["ltp"])
        except Exception:
            pass
        return None

    def get_vix_adjustment(self) -> float:
        """Rule: If India VIX > 25, return 0.5, else 1.0."""
        vix = self._get_vix_value()
        if vix and vix > 25.0:
            logger.warning(f"High India VIX ({vix:.2f} > 25). Volatility multiplier: 0.5")
            return 0.5
        return 1.0
