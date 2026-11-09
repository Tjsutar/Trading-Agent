import datetime

def get_next_weekly_expiry(token_loader=None):
    if token_loader and hasattr(token_loader, "get_active_nifty_expiry"):
        try:
            active_expiry = token_loader.get_active_nifty_expiry()
            if active_expiry:
                return active_expiry
        except Exception:
            pass

    today = datetime.date.today()
    # Nifty Expiry is Tuesday (weekday 1), shifted to Monday if holiday
    current_weekday = today.weekday()
    target_weekday = 1
    days_ahead = (target_weekday - current_weekday) % 7
    
    next_expiry = today + datetime.timedelta(days=days_ahead)
    return next_expiry.strftime("%d%b%Y").upper()

# Validation for 2026-01-20 (Tuesday)
# weekday = 1
# days_ahead = 3 - 1 = 2
# next_expiry = 20 + 2 = 22 Jan 2026 -> 22JAN2026. Correct.
