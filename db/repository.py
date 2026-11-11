"""
Data repository pattern for T-Agent trades, signals, SL modifications, and audit logs.
"""
import json
from datetime import datetime, timezone, date
from typing import List, Optional, Dict, Any
from sqlalchemy import func
from db.database import get_db_session
from db.models import Trade, Signal, SLModification, AuditLog, StrategyConfig


class TradeRepository:
    """Repository handling all database persistence operations."""

    @staticmethod
    def record_signal(
        strategy_name: str,
        symbol: str,
        timeframe: str,
        signal_type: str,
        confidence: float = 100.0,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        risk_reward: Optional[float] = None,
        executed: bool = False,
        rejection_reason: Optional[str] = None,
        liquidity_zone_id: Optional[str] = None
    ) -> Signal:
        """Persist a newly generated trading signal."""
        with get_db_session() as session:
            sig = Signal(
                strategy_name=strategy_name,
                symbol=symbol,
                timeframe=timeframe,
                signal_type=signal_type,
                confidence=confidence,
                liquidity_zone_id=liquidity_zone_id,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward=risk_reward,
                executed=executed,
                rejection_reason=rejection_reason
            )
            session.add(sig)
            session.flush()
            session.refresh(sig)
            return sig

    @staticmethod
    def create_trade(
        order_id: str,
        strategy_name: str,
        symbol: str,
        trade_type: str,
        lot_size: float,
        entry_price: float,
        initial_stop_loss: Optional[float] = None,
        take_profit_1: Optional[float] = None,
        take_profit_2: Optional[float] = None,
        breakeven_enabled: bool = False,
        trailing_sl_enabled: bool = False,
        trailing_distance_pips: Optional[float] = None,
        trailing_mode: str = "fixed"
    ) -> Trade:
        """Create and persist a new trade."""
        with get_db_session() as session:
            trade = Trade(
                order_id=order_id,
                strategy_name=strategy_name,
                symbol=symbol,
                trade_type=trade_type,
                lot_size=lot_size,
                entry_price=entry_price,
                initial_stop_loss=initial_stop_loss,
                current_stop_loss=initial_stop_loss,
                take_profit_1=take_profit_1,
                take_profit_2=take_profit_2,
                status="OPEN",
                breakeven_enabled=breakeven_enabled,
                trailing_sl_enabled=trailing_sl_enabled,
                trailing_distance_pips=trailing_distance_pips,
                trailing_mode=trailing_mode,
                max_favorable_price=entry_price
            )
            session.add(trade)
            session.flush()
            session.refresh(trade)
            return trade

    @staticmethod
    def close_trade(
        order_id: str,
        exit_price: float,
        pnl: float,
        reason: str = "TARGET_OR_SL",
        commission: float = 0.0
    ) -> Optional[Trade]:
        """Close an active trade and record exit price, Net PnL, and total charges."""
        with get_db_session() as session:
            trade = session.query(Trade).filter(Trade.order_id == order_id).first()
            if trade:
                trade.exit_price = exit_price
                trade.exit_time = datetime.now(timezone.utc)
                trade.pnl = pnl
                trade.commission = commission
                trade.status = "CLOSED"
                trade.updated_at = datetime.now(timezone.utc)
                session.flush()
                session.refresh(trade)
                
                # Also log audit
                TradeRepository.record_audit(
                    event_type="TRADE_CLOSED",
                    severity="INFO",
                    message=f"Trade {order_id} ({trade.symbol}) closed at {exit_price} with Net PnL {pnl:.2f} (Charges: ₹{commission:.2f}). Reason: {reason}",
                    context={"order_id": order_id, "exit_price": exit_price, "pnl": pnl, "commission": commission, "reason": reason}
                )
                return trade
            return None

    @staticmethod
    def record_sl_modification(
        order_id: str,
        old_sl: float,
        new_sl: float,
        current_price: float,
        reason: str
    ) -> Optional[SLModification]:
        """Record a stop loss adjustment (Breakeven or Trailing)."""
        with get_db_session() as session:
            trade = session.query(Trade).filter(Trade.order_id == order_id).first()
            trade_id = trade.id if trade else None

            sl_mod = SLModification(
                order_id=order_id,
                trade_id=trade_id,
                old_sl=old_sl,
                new_sl=new_sl,
                current_price=current_price,
                reason=reason
            )
            session.add(sl_mod)

            if trade:
                trade.current_stop_loss = new_sl
                trade.sl_modification_count = (trade.sl_modification_count or 0) + 1
                if reason == "BREAKEVEN":
                    trade.breakeven_activated = True
                    trade.breakeven_activation_price = current_price
                elif "TRAIL" in reason.upper():
                    trade.trailing_sl_activated = True
                    if trade.trailing_activation_price is None:
                        trade.trailing_activation_price = current_price
                
                # Update max favorable price
                if trade.trade_type == "BUY":
                    if trade.max_favorable_price is None or current_price > trade.max_favorable_price:
                        trade.max_favorable_price = current_price
                else:
                    if trade.max_favorable_price is None or current_price < trade.max_favorable_price:
                        trade.max_favorable_price = current_price
                
                trade.updated_at = datetime.now(timezone.utc)

            session.flush()
            session.refresh(sl_mod)
            return sl_mod

    @staticmethod
    def get_open_trades() -> List[Trade]:
        """Fetch all currently open trades for monitoring/recovery."""
        with get_db_session() as session:
            return session.query(Trade).filter(Trade.status == "OPEN").all()

    @staticmethod
    def get_daily_realized_pnl(target_date: Optional[date] = None) -> float:
        """Calculate total realized PnL for a given date (defaults to today)."""
        target = target_date or datetime.now(timezone.utc).date()
        with get_db_session() as session:
            total = session.query(func.sum(Trade.pnl)).filter(
                Trade.status == "CLOSED",
                func.date(Trade.exit_time) == target
            ).scalar()
            return float(total or 0.0)

    @staticmethod
    def get_daily_charges(target_date: Optional[date] = None) -> float:
        """Calculate total statutory & brokerage charges for a given date."""
        target = target_date or datetime.now(timezone.utc).date()
        with get_db_session() as session:
            total = session.query(func.sum(Trade.commission)).filter(
                Trade.status == "CLOSED",
                func.date(Trade.exit_time) == target
            ).scalar()
            return float(total or 0.0)

    @staticmethod
    def get_daily_gross_pnl(target_date: Optional[date] = None) -> float:
        """Calculate total gross PnL (before trade charges) for a given date."""
        target = target_date or datetime.now(timezone.utc).date()
        with get_db_session() as session:
            trades = session.query(Trade).filter(
                Trade.status == "CLOSED",
                func.date(Trade.exit_time) == target
            ).all()
            total_gross = sum(
                round((t.exit_price - t.entry_price) * t.lot_size, 2)
                for t in trades if t.exit_price and t.entry_price
            )
            return float(total_gross)

    @staticmethod
    def had_loss_today(target_date: Optional[date] = None) -> bool:
        """Check if any closed trade today resulted in a loss."""
        target = target_date or datetime.now(timezone.utc).date()
        with get_db_session() as session:
            count = session.query(func.count(Trade.id)).filter(
                Trade.status == "CLOSED",
                func.date(Trade.exit_time) == target,
                Trade.pnl < 0
            ).scalar()
            return (count or 0) > 0

    @staticmethod
    def record_audit(
        event_type: str,
        severity: str,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> AuditLog:
        """Record an institutional audit log entry."""
        with get_db_session() as session:
            log = AuditLog(
                event_type=event_type,
                severity=severity,
                message=message,
                context_data=json.dumps(context) if context else None
            )
            session.add(log)
            session.flush()
            session.refresh(log)
            return log

