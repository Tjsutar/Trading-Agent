"""
Option Trade Statutory & Brokerage Charges Calculator (BRD FR-SL-002).
Accurately computes Indian options trading charges conforming to Angel One SmartAPI / NSE NFO tariff:
  1. Brokerage: Flat ₹20 per executed order (₹40 round-trip buy + sell).
  2. STT (Securities Transaction Tax): 0.1% on Sell option premium turnover.
  3. Exchange Transaction Charges: 0.03503% on total premium turnover (Buy + Sell).
  4. GST: 18% on (Brokerage + Exchange Transaction charges + SEBI charges).
  5. SEBI Turnover Fee: ₹10 per crore (0.0001%) on total premium turnover.
  6. Stamp Duty: 0.003% on Buy side premium turnover.
"""
from typing import Dict, Any


def calculate_option_charges(entry_price: float, exit_price: float, qty: int) -> Dict[str, Any]:
    """
    Computes gross P&L, full statutory charges breakdown, and net P&L.
    """
    buy_turnover = float(entry_price) * int(qty)
    sell_turnover = float(exit_price) * int(qty)
    total_turnover = buy_turnover + sell_turnover

    # 1. Brokerage (Angel One standard: ₹20 buy + ₹20 sell)
    brokerage = 40.0

    # 2. STT: 0.1% on sell turnover
    stt = round(sell_turnover * 0.001, 2)

    # 3. Exchange transaction charges: 0.03503% on total turnover
    exchange_charges = round(total_turnover * 0.0003503, 2)

    # 4. SEBI turnover fee: ₹10 per crore
    sebi_charges = round(total_turnover * 0.000001, 2)

    # 5. GST: 18% on (Brokerage + Exchange Charges + SEBI Charges)
    gst = round((brokerage + exchange_charges + sebi_charges) * 0.18, 2)

    # 6. Stamp duty: 0.003% on buy turnover
    stamp_duty = round(buy_turnover * 0.00003, 2)

    total_charges = round(brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty, 2)
    gross_pnl = round((float(exit_price) - float(entry_price)) * int(qty), 2)
    net_pnl = round(gross_pnl - total_charges, 2)

    return {
        "gross_pnl": gross_pnl,
        "total_charges": total_charges,
        "net_pnl": net_pnl,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "gst": gst,
        "stamp_duty": stamp_duty,
        "sebi_charges": sebi_charges
    }
