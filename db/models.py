"""
SQLAlchemy ORM models conforming to BRD Section 6 (Trade Database Schema).
Supports PostgreSQL and SQLite.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), unique=True, nullable=False, index=True)
    strategy_name = Column(String(64), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    trade_type = Column(String(8), nullable=False)  # BUY or SELL
    lot_size = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    
    initial_stop_loss = Column(Float, nullable=True)
    current_stop_loss = Column(Float, nullable=True)
    take_profit_1 = Column(Float, nullable=True)
    take_profit_2 = Column(Float, nullable=True)
    
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True, index=True)
    pnl = Column(Float, nullable=True)
    commission = Column(Float, default=0.0)
    status = Column(String(20), default="OPEN", index=True)  # OPEN, CLOSED, CANCELLED
    
    # Breakeven tracking
    breakeven_enabled = Column(Boolean, default=False)
    breakeven_activated = Column(Boolean, default=False)
    breakeven_activation_price = Column(Float, nullable=True)
    
    # Trailing SL tracking
    trailing_sl_enabled = Column(Boolean, default=False)
    trailing_sl_activated = Column(Boolean, default=False)
    trailing_activation_price = Column(Float, nullable=True)
    trailing_distance_pips = Column(Float, nullable=True)
    trailing_mode = Column(String(20), default="fixed")  # fixed, atr, percentage
    max_favorable_price = Column(Float, nullable=True)
    max_profit_reached = Column(Float, default=0.0)
    sl_modification_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    sl_modifications = relationship("SLModification", back_populates="trade", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Trade(order_id='{self.order_id}', symbol='{self.symbol}', type='{self.trade_type}', status='{self.status}')>"


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(64), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False)  # M1, M5, H1, etc.
    signal_type = Column(String(8), nullable=False)  # BUY or SELL
    confidence = Column(Float, default=100.0)
    liquidity_zone_id = Column(String(64), nullable=True)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    risk_reward = Column(Float, nullable=True)
    executed = Column(Boolean, default=False)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        return f"<Signal(strategy='{self.strategy_name}', symbol='{self.symbol}', type='{self.signal_type}', executed={self.executed})>"


class SLModification(Base):
    __tablename__ = "sl_modifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), nullable=False, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=True)
    old_sl = Column(Float, nullable=False)
    new_sl = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    reason = Column(String(128), nullable=True)  # 'BREAKEVEN', 'STEP_LADDER_TRAIL', 'MANUAL'
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    trade = relationship("Trade", back_populates="sl_modifications")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, index=True)  # KILL_SWITCH, MARGIN_CHECK, ORDER, SESSION, ERROR
    severity = Column(String(16), default="INFO", index=True)  # INFO, WARNING, CRITICAL
    message = Column(Text, nullable=False)
    context_data = Column(Text, nullable=True)  # JSON formatted context
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        return f"<AuditLog(type='{self.event_type}', severity='{self.severity}', msg='{self.message[:30]}...')>"


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    parameters = Column(Text, nullable=False)  # JSON text
    is_active = Column(Boolean, default=True)
    capital_allocation_pct = Column(Float, default=100.0)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
