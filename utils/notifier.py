"""
External Push Notification Service (BRD FR-MR-001).
Broadcasts real-time alerts for Order Executions, Breakeven SL moves,
Kill-Switch events, and Daily P&L tear sheets to Telegram and Discord.
"""
import os
import requests
from typing import Optional, Dict, Any
from config.settings import Config
from utils.logger import get_logger

logger = get_logger("Notifier")


class AlertNotifier:
    """
    Thread-safe push notification dispatcher for Telegram and Discord webhooks.
    """

    def __init__(self):
        self.telegram_token = Config.TELEGRAM_BOT_TOKEN
        self.telegram_chat_id = Config.TELEGRAM_CHAT_ID
        self.discord_webhook = Config.DISCORD_WEBHOOK_URL

    def _send_telegram(self, message: str):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=3.0)
        except Exception as e:
            logger.debug(f"Telegram notification error: {e}")

    def _send_discord(self, message: str):
        if not self.discord_webhook:
            return
        try:
            payload = {"content": message}
            requests.post(self.discord_webhook, json=payload, timeout=3.0)
        except Exception as e:
            logger.debug(f"Discord notification error: {e}")

    def broadcast(self, message: str):
        """Sends message to all configured external endpoints and logs locally."""
        logger.info(f"📢 [Alert] {message}")
        self._send_telegram(message)
        self._send_discord(message)

    def notify_trade_entry(self, symbol: str, side: str, qty: int, price: float, sl: float, tp: float):
        msg = (
            f"🚀 *TRADE ENTRY DISPATCHED*\n"
            f"• *Contract*: `{symbol}`\n"
            f"• *Side*: {side} | *Qty*: {qty}\n"
            f"• *Entry Price*: ₹{price:.2f}\n"
            f"• *Initial Stop Loss*: ₹{sl:.2f} (-16 pts)\n"
            f"• *Target*: ₹{tp:.2f} (+36 pts | 1:2.25 RR)"
        )
        self.broadcast(msg)

    def notify_sl_moved(self, symbol: str, old_sl: float, new_sl: float, reason: str = "BREAKEVEN"):
        msg = (
            f"🔒 *PROTECTION ACTIVE: SL ESCALATED*\n"
            f"• *Contract*: `{symbol}`\n"
            f"• *Old SL*: ₹{old_sl:.2f} ➔ *New SL*: ₹{new_sl:.2f}\n"
            f"• *Status*: Risk-Free Scratch Guaranteed ({reason})"
        )
        self.broadcast(msg)

    def notify_trade_exit(self, symbol: str, exit_price: float, pnl: float, reason: str):
        badge = "🟢 WIN" if pnl > 0 else "🔴 LOSS"
        msg = (
            f"🏁 *TRADE CLOSED: {badge}*\n"
            f"• *Contract*: `{symbol}`\n"
            f"• *Exit Price*: ₹{exit_price:.2f}\n"
            f"• *Realized P&L*: {'+' if pnl>=0 else ''}₹{pnl:,.2f} (After Fees)\n"
            f"• *Exit Reason*: `{reason}`"
        )
        self.broadcast(msg)

    def notify_kill_switch(self, daily_pnl: float, cap: float):
        msg = (
            f"🛑 *CRITICAL: KILL SWITCH TRIGGERED*\n"
            f"• *Daily Drawdown*: ₹{daily_pnl:,.2f}\n"
            f"• *Allowed Cap*: ₹{cap:,.2f}\n"
            f"• *Action*: All open positions flattened. Trading halted until next session."
        )
        self.broadcast(msg)

    def notify_daily_summary(self, capital: float, daily_pnl: float, trades: int, wins: int, losses: int):
        roi = (daily_pnl / capital) * 100.0 if capital > 0 else 0.0
        msg = (
            f"📊 *DAILY SESSION SUMMARY*\n"
            f"• *Account Capital*: ₹{capital:,.2f}\n"
            f"• *Realized P&L*: {'+' if daily_pnl>=0 else ''}₹{daily_pnl:,.2f} ({roi:+.1f}%)\n"
            f"• *Trades*: {trades} ({wins}W / {losses}L)\n"
            f"• *Status*: Hibernating until 09:10 IST next trading day."
        )
        self.broadcast(msg)


# Global singleton instance
notifier = AlertNotifier()

