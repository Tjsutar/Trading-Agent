"""
Real-time CLI Telemetry Dashboard (BRD FR-MR-004).
Displays live P&L, system audit events, signals, and open positions from the database.
"""
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Add workspace root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import get_db_session, init_db
from db.models import Trade, Signal, AuditLog
from db.repository import TradeRepository


def display_dashboard():
    init_db()
    print("\n" + "=" * 60)
    print("      📊 T-AGENT REAL-TIME TELEMETRY DASHBOARD")
    print("=" * 60)
    
    with get_db_session() as session:
        # 1. Daily PnL & Equity
        daily_pnl = TradeRepository.get_daily_realized_pnl()
        pnl_badge = f"+₹{daily_pnl:,.2f}" if daily_pnl >= 0 else f"-₹{abs(daily_pnl):,.2f}"
        status_color = "🟢 PROFITABLE" if daily_pnl >= 0 else "🔴 LOSS"
        print(f" Daily Realized P&L: {pnl_badge} [{status_color}]")

        # 2. Open Positions
        open_trades = session.query(Trade).filter(Trade.status == "OPEN").all()
        print(f" Active Positions:   {len(open_trades)}")
        if open_trades:
            print("\n [ Open Positions ]")
            for t in open_trades:
                sl_info = f"SL: {t.current_stop_loss}" if t.current_stop_loss else "SL: None"
                be_info = "🔒 BE: Active" if t.breakeven_activated else "BE: Inactive"
                tsl_info = "🚀 TSL: Active" if t.trailing_sl_activated else "TSL: Inactive"
                print(f"  • {t.symbol} | Type: {t.trade_type} | Qty: {t.lot_size} | Entry: ₹{t.entry_price:,.2f} | {sl_info} | {be_info} | {tsl_info}")

        # 3. Recent Signals
        recent_signals = session.query(Signal).order_by(Signal.created_at.desc()).limit(5).all()
        print("\n [ Recent Generated Signals ]")
        if recent_signals:
            for s in recent_signals:
                exec_mark = "✅ Executed" if s.executed else f"❌ Rejected ({s.rejection_reason or 'Filter'})"
                print(f"  • [{s.signal_type}] {s.symbol} ({s.timeframe}) | Entry: ₹{s.entry_price or 0:,.2f} | RR: 1:{s.risk_reward or 0} | {exec_mark}")
        else:
            print("  (No signals generated yet)")

        # 4. System Audit Trail
        recent_audits = session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(5).all()
        print("\n [ System Health & Audit Trail ]")
        if recent_audits:
            for a in recent_audits:
                time_str = a.created_at.strftime("%H:%M:%S") if a.created_at else "Now"
                print(f"  • [{time_str}] [{a.severity}] {a.event_type}: {a.message}")
        else:
            print("  (No audit logs recorded)")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    display_dashboard()
