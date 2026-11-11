"""
T-Agent Visual Web Dashboard & Telemetry Server (BRD FR-MR-004).
Built with Python's standard http.server for zero-dependency local execution.
Provides real-time dark-mode dashboard displaying:
  1. System Health & Broker Status (Client T69288, Session Resiliency)
  2. Financial Metrics (Daily Realized P&L, Win Rate, Capital Tier, Loss Cap)
  3. Today's Completed Trades History with entry/exit prices, net P&L, and reasons
  4. Active Positions with Breakeven & Trailing Stop badges
  5. Real-time Generated Signals (SMC, Momentum)
  6. Institutional Audit Trail
  7. Emergency Kill Switch Control
"""
import sys
import os
import json
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def to_ist_display(dt):
    if not dt:
        return "--"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_ist = dt.astimezone(IST)
    now_ist = datetime.now(IST)
    if dt_ist.date() == now_ist.date():
        return dt_ist.strftime("%H:%M:%S")
    return dt_ist.strftime("%d-%b %H:%M:%S")

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import get_db_session, init_db
from db.models import Trade, Signal, AuditLog
from db.repository import TradeRepository
from config.settings import Config
from utils.logger import get_logger

logger = get_logger("WebDashboard")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>T-Agent | Institutional Trading Telemetry</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --card-border: #1f2937;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #3b82f6;
      --green: #10b981;
      --red: #ef4444;
      --amber: #f59e0b;
      --purple: #8b5cf6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: 'Inter', -apple-system, sans-serif;
      padding: 24px;
      line-height: 1.5;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--card-border);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand h1 {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .brand .badge {
      background: rgba(59, 130, 246, 0.15);
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
    }
    .status-pill {
      display: flex;
      align-items: center;
      gap: 8px;
      background: rgba(16, 185, 129, 0.15);
      color: var(--green);
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 600;
    }
    .pulse-dot {
      width: 8px;
      height: 8px;
      background: var(--green);
      border-radius: 50%;
      box-shadow: 0 0 8px var(--green);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .grid-4 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
      transition: border-color 0.2s;
    }
    .card:hover { border-color: #374151; }
    .card-title {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 8px;
    }
    .card-value {
      font-size: 26px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }
    .card-sub {
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 4px;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 24px;
    }
    @media (max-width: 960px) { .grid-2 { grid-template-columns: 1fr; } }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }
    .panel-header h2 { font-size: 16px; font-weight: 600; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th {
      text-align: left;
      padding: 10px 12px;
      color: var(--text-muted);
      border-bottom: 1px solid var(--card-border);
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
    }
    td {
      padding: 12px;
      border-bottom: 1px solid var(--card-border);
    }
    .tag {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
    }
    .tag-buy { background: rgba(16, 185, 129, 0.15); color: var(--green); }
    .tag-sell { background: rgba(239, 68, 68, 0.15); color: var(--red); }
    .tag-active { background: rgba(59, 130, 246, 0.15); color: var(--accent); }
    .tag-warn { background: rgba(245, 158, 11, 0.15); color: var(--amber); }
    .tag-purple { background: rgba(139, 92, 246, 0.15); color: var(--purple); }
    .audit-item {
      display: flex;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--card-border);
      font-size: 12px;
    }
    .audit-time {
      font-family: 'JetBrains Mono', monospace;
      color: var(--text-muted);
      min-width: 65px;
    }
    .audit-msg { flex: 1; }
    .btn-danger {
      background: #dc2626;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }
    .btn-danger:hover { background: #b91c1c; }
    .badge-win {
      background: rgba(16, 185, 129, 0.2);
      color: var(--green);
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 6px;
      font-family: 'JetBrains Mono', monospace;
    }
    .badge-loss {
      background: rgba(239, 68, 68, 0.2);
      color: var(--red);
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 6px;
      font-family: 'JetBrains Mono', monospace;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="brand">
      <h1>T-Agent Institutional Telemetry</h1>
      <span class="badge">Angel One SmartAPI</span>
      <span class="badge" style="background: rgba(139, 92, 246, 0.15); color: var(--purple);">Client: T69288</span>
      <span class="badge" style="background: rgba(16, 185, 129, 0.15); color: var(--green);">Path A: ₹15,000 Tier</span>
    </div>
    <div style="display: flex; gap: 12px; align-items: center;">
      <div class="status-pill">
        <div class="pulse-dot"></div>
        <span id="engine-status">WebSocket 2.0 Streaming</span>
      </div>
      <button class="btn-danger" onclick="triggerKillSwitch()">Emergency Kill Switch</button>
    </div>
  </div>

  <div class="grid-4">
    <div class="card">
      <div class="card-title">Daily Realized P&L</div>
      <div class="card-value" id="daily-pnl" style="color: var(--green);">+₹0.00</div>
      <div class="card-sub" id="pnl-sub">Target: ₹5,000.00 | Tier: ₹15k</div>
    </div>
    <div class="card">
      <div class="card-title">Today's Win Rate</div>
      <div class="card-value" id="win-rate" style="color: var(--green);">0%</div>
      <div class="card-sub" id="win-rate-sub">0 Wins / 0 Losses</div>
    </div>
    <div class="card">
      <div class="card-title">Active Positions</div>
      <div class="card-value" id="active-positions-count">0 Open</div>
      <div class="card-sub">Max 1 Active at a Time 🛡️</div>
    </div>
    <div class="card">
      <div class="card-title">Daily Profit Target</div>
      <div class="card-value" id="target-profit" style="color: var(--green);">₹5,000.00</div>
      <div class="card-sub" id="target-profit-sub" style="color: var(--green);">Next Trade Allowed on Win 🎯</div>
    </div>
  </div>

  <!-- Today's Executed Trades & P&L History (Prominent) -->
  <div class="card" style="margin-bottom: 24px;">
    <div class="panel-header">
      <h2>📊 Today's Completed Trades & P&L History</h2>
      <span class="card-sub" id="closed-trades-count" style="font-weight: 600; color: var(--green);">0 trades</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Entry Time (IST)</th>
          <th>Exit Time (IST)</th>
          <th>Order ID</th>
          <th>Contract Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Entry Price</th>
          <th>Exit Price</th>
          <th>Return (%)</th>
          <th>Net P&L (₹)</th>
          <th>Outcome</th>
        </tr>
      </thead>
      <tbody id="closed-trades-tbody">
        <tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 24px;">No closed trades today yet.</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Active Positions Table -->
  <div class="card" style="margin-bottom: 24px;">
    <div class="panel-header">
      <h2>💼 Active Positions & Dynamic Trailing Stop</h2>
      <span class="card-sub" id="open-positions-subtitle">Zero Open Risk</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Entry Time (IST)</th>
          <th>Order ID</th>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Entry Price</th>
          <th>Current SL</th>
          <th>Breakeven Status</th>
          <th>Trailing Stop Status</th>
        </tr>
      </thead>
      <tbody id="positions-tbody">
        <tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">No open positions currently active.</td></tr>
      </tbody>
    </table>
  </div>

  <div class="grid-2">
    <div class="card">
      <div class="panel-header">
        <h2>⚡ Live Generated Signals (SMC & Momentum)</h2>
        <span class="card-sub" id="signals-count">0 signals</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Symbol</th>
            <th>Type</th>
            <th>Entry</th>
            <th>R:R</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="signals-tbody">
          <tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 24px;">No signals generated yet.</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="panel-header">
        <h2>🛡️ Institutional Audit & Safety Trail</h2>
        <span class="card-sub">Real-time log stream</span>
      </div>
      <div id="audit-list">
        <!-- Injected via JS -->
      </div>
    </div>
  </div>

  <script>
    async function updateDashboard() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();

        // 1. Update Daily Realized P&L
        const pnlEl = document.getElementById('daily-pnl');
        const isPos = data.daily_pnl >= 0;
        pnlEl.textContent = (isPos ? '+₹' : '-₹') + Math.abs(data.daily_pnl).toFixed(2);
        pnlEl.style.color = isPos ? 'var(--green)' : 'var(--red)';
        const targetVal = data.daily_profit_target || 5000;
        const targetPct = Math.min(100, (data.daily_pnl / targetVal) * 100).toFixed(1);
        document.getElementById('pnl-sub').textContent = `Target: ₹${targetVal.toFixed(2)} (${targetPct}% achieved)`;

        // Update Profit Target Subtitle
        const targetSubEl = document.getElementById('target-profit-sub');
        if (targetSubEl) {
          if (data.daily_pnl >= targetVal) {
            targetSubEl.innerHTML = '🎉 Target Reached! Locking gains 🔒';
            targetSubEl.style.color = 'var(--green)';
          } else if (data.had_loss_today) {
            targetSubEl.innerHTML = '🛑 Loss Taken: Capital Protected';
            targetSubEl.style.color = 'var(--red)';
          } else {
            targetSubEl.innerHTML = '✅ Next Trade Allowed on Win 🎯';
            targetSubEl.style.color = 'var(--green)';
          }
        }

        // 2. Update Win Rate
        const winEl = document.getElementById('win-rate');
        winEl.textContent = data.win_rate + '%';
        winEl.style.color = data.win_rate >= 50 ? 'var(--green)' : (data.total_trades === 0 ? 'var(--text-muted)' : 'var(--red)');
        document.getElementById('win-rate-sub').textContent = `${data.wins} Win / ${data.losses} Loss (${data.total_trades} Trade${data.total_trades === 1 ? '' : 's'})`;

        // 3. Update Active Positions Count
        document.getElementById('active-positions-count').textContent = data.open_trades.length + ' Open';

        // 4. Render Today's Closed Trades History
        const closedTbody = document.getElementById('closed-trades-tbody');
        document.getElementById('closed-trades-count').textContent = `${data.closed_trades.length} trade(s) completed today`;
        if (data.closed_trades.length > 0) {
          closedTbody.innerHTML = data.closed_trades.map(t => {
            const isWin = t.pnl >= 0;
            const pnlColor = isWin ? 'var(--green)' : 'var(--red)';
            const sign = isWin ? '+' : '';
            return `
              <tr>
                <td style="color:var(--text); font-size:12px; font-family:'JetBrains Mono'; font-weight:600;">${t.entry_time}</td>
                <td style="color:var(--text-muted); font-size:12px; font-family:'JetBrains Mono'; font-weight:600;">${t.exit_time}</td>
                <td style="font-family:'JetBrains Mono'; font-size:11px; color:var(--text-muted);">${t.order_id}</td>
                <td><strong style="color:var(--text);">${t.symbol}</strong></td>
                <td><span class="tag tag-${t.side.toLowerCase()}">${t.side}</span></td>
                <td style="font-family:'JetBrains Mono'">${t.qty}</td>
                <td style="font-family:'JetBrains Mono'">₹${(t.entry || 0).toFixed(2)}</td>
                <td style="font-family:'JetBrains Mono'">₹${(t.exit || 0).toFixed(2)}</td>
                <td style="font-family:'JetBrains Mono'; color:${pnlColor}; font-weight:600;">${sign}${t.roi_pct}%</td>
                <td style="font-family:'JetBrains Mono'; font-weight:700; color:${pnlColor}; font-size:14px;">${sign}₹${t.pnl.toFixed(2)}</td>
                <td><span class="${isWin ? 'badge-win' : 'badge-loss'}">${isWin ? '🟢 WIN' : '🔴 LOSS'}</span></td>
              </tr>
            `;
          }).join('');
        } else {
          closedTbody.innerHTML = '<tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 24px;">No closed trades today yet.</td></tr>';
        }

        // 5. Render Active Positions
        const posTbody = document.getElementById('positions-tbody');
        if (data.open_trades.length > 0) {
          document.getElementById('open-positions-subtitle').textContent = 'Live Market Active Trade';
          posTbody.innerHTML = data.open_trades.map(p => `
            <tr>
              <td style="color:var(--text); font-size:12px; font-family:'JetBrains Mono'; font-weight:600;">${p.entry_time || '--'}</td>
              <td style="font-family:'JetBrains Mono'; font-size:11px;">${p.order_id}</td>
              <td><strong>${p.symbol}</strong></td>
              <td><span class="tag tag-${p.side.toLowerCase()}">${p.side}</span></td>
              <td>${p.qty}</td>
              <td style="font-family:'JetBrains Mono'">₹${(p.entry || 0).toFixed(2)}</td>
              <td style="font-family:'JetBrains Mono'">₹${(p.sl || 0).toFixed(2)}</td>
              <td><span class="tag ${p.be ? 'tag-buy' : 'tag-warn'}">${p.be ? 'Locked 🔒 (+2 pts)' : 'Pending (+10 pts)'}</span></td>
              <td><span class="tag ${p.tsl ? 'tag-buy' : 'tag-purple'}">${p.tsl ? 'Trailing 🚀 (-8 pts)' : 'Active'}</span></td>
            </tr>
          `).join('');
        } else {
          document.getElementById('open-positions-subtitle').textContent = 'Zero Open Risk (Sitting in Cash)';
          posTbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">No open positions currently active. Capital 100% protected.</td></tr>';
        }

        // 6. Update Signals
        document.getElementById('signals-count').textContent = data.signals.length + ' signals';
        const sigTbody = document.getElementById('signals-tbody');
        if (data.signals.length > 0) {
          sigTbody.innerHTML = data.signals.map(s => `
            <tr>
              <td><strong>${s.strategy}</strong></td>
              <td>${s.symbol} <span style="color:var(--text-muted)">(${s.timeframe})</span></td>
              <td><span class="tag tag-${s.type.toLowerCase()}">${s.type}</span></td>
              <td style="font-family:'JetBrains Mono'">₹${(s.entry || 0).toFixed(2)}</td>
              <td style="font-family:'JetBrains Mono'">1:${s.rr || '2.0'}</td>
              <td><span class="tag ${s.executed ? 'tag-buy' : 'tag-warn'}">${s.executed ? 'Executed ⚡' : 'Filtered 🛡️'}</span></td>
            </tr>
          `).join('');
        }

        // 7. Update Audit Log
        const auditList = document.getElementById('audit-list');
        if (data.audits.length > 0) {
          auditList.innerHTML = data.audits.map(a => `
            <div class="audit-item">
              <span class="audit-time">${a.time}</span>
              <span class="tag ${a.severity === 'CRITICAL' ? 'tag-sell' : (a.severity === 'WARNING' ? 'tag-warn' : 'tag-active')}">${a.severity}</span>
              <span class="audit-msg"><strong>${a.type}</strong>: ${a.message}</span>
            </div>
          `).join('');
        }
      } catch (err) {
        console.error('Failed to poll dashboard data:', err);
      }
    }

    async function triggerKillSwitch() {
      if (confirm('⚠️ WARNING: Trigger institutional Kill Switch to immediately halt all bot operations?')) {
        const res = await fetch('/api/kill-switch', { method: 'POST' });
        const resData = await res.json();
        alert('Kill Switch Response: ' + resData.message);
        updateDashboard();
      }
    }

    setInterval(updateDashboard, 2000);
    updateDashboard();
  </script>
</body>
</html>
"""


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            init_db()
            with get_db_session() as session:
                daily_pnl = TradeRepository.get_daily_realized_pnl()
                trades = session.query(Trade).filter(Trade.status == "OPEN").all()
                closed_trades = session.query(Trade).filter(Trade.status == "CLOSED").order_by(Trade.id.desc()).limit(20).all()
                signals = session.query(Signal).order_by(Signal.id.desc()).limit(10).all()
                audits = session.query(AuditLog).order_by(AuditLog.id.desc()).limit(8).all()

                wins = sum(1 for t in closed_trades if (t.pnl or 0) > 0)
                losses = sum(1 for t in closed_trades if (t.pnl or 0) < 0)
                total = len(closed_trades)
                win_rate = round((wins / total * 100), 1) if total > 0 else 0.0

                had_loss = TradeRepository.had_loss_today()
                payload = {
                    "daily_pnl": daily_pnl,
                    "daily_profit_target": Config.DAILY_PROFIT_TARGET,
                    "had_loss_today": had_loss,
                    "next_trade_allowed": (not had_loss) and (daily_pnl < Config.DAILY_PROFIT_TARGET) and (len(trades) == 0),
                    "wins": wins,
                    "losses": losses,
                    "total_trades": total,
                    "win_rate": win_rate,
                    "open_trades": [
                        {
                            "order_id": t.order_id,
                            "symbol": t.symbol,
                            "side": t.trade_type,
                            "qty": int(t.lot_size),
                            "entry": t.entry_price,
                            "sl": t.current_stop_loss,
                            "be": t.breakeven_activated,
                            "tsl": t.trailing_sl_activated,
                            "entry_time": to_ist_display(t.entry_time)
                        }
                        for t in trades
                    ],
                    "closed_trades": [
                        {
                            "id": t.id,
                            "order_id": t.order_id,
                            "symbol": t.symbol,
                            "side": t.trade_type,
                            "qty": int(t.lot_size),
                            "entry": t.entry_price,
                            "exit": t.exit_price,
                            "pnl": round(t.pnl, 2) if t.pnl is not None else 0.0,
                            "entry_time": to_ist_display(t.entry_time),
                            "exit_time": to_ist_display(t.exit_time),
                            "roi_pct": round(((t.exit_price - t.entry_price) / t.entry_price) * 100, 1) if (t.entry_price and t.exit_price) else 0.0
                        }
                        for t in closed_trades
                    ],
                    "signals": [
                        {
                            "strategy": s.strategy_name,
                            "symbol": s.symbol,
                            "timeframe": s.timeframe,
                            "type": s.signal_type,
                            "entry": s.entry_price,
                            "rr": s.risk_reward,
                            "executed": s.executed
                        }
                        for s in signals
                    ],
                    "audits": [
                        {
                            "time": to_ist_display(a.created_at),
                            "severity": a.severity,
                            "type": a.event_type,
                            "message": a.message
                        }
                        for a in audits
                    ]
                }
                self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/kill-switch":
            TradeRepository.record_audit(
                event_type="MANUAL_KILL_SWITCH",
                severity="CRITICAL",
                message="Emergency Kill Switch triggered manually from Web Dashboard."
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "HALTED", "message": "Manual Kill Switch triggered."}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence routine HTTP polling logs
        pass


def run_dashboard_server(port: int = 8080):
    init_db()
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, DashboardRequestHandler)
    print("\n" + "=" * 60)
    print(f"  🌐 T-AGENT VISUAL WEB DASHBOARD RUNNING")
    print(f"  👉 Open URL: http://127.0.0.1:{port}")
    print("=" * 60 + "\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_dashboard_server(port)
