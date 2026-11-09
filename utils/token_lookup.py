import datetime
import requests
import pandas as pd
from config.settings import Config

class TokenLookup:
    def __init__(self):
        self.df = None

    def load_scrip_master(self):
        """Downloads the huge JSON file from Angel One once"""
        print(">>> [Data] Downloading Scrip Master (This may take 10s)...")
        try:
            response = requests.get(Config.SCRIP_MASTER_URL)
            data = response.json()
            self.df = pd.DataFrame(data)
            
            # Optimization: Convert 'strike' to float once for accurate comparison
            # Angel One 'strike' is in paise (e.g. 2300000.00)
            self.df['strike'] = pd.to_numeric(self.df['strike'], errors='coerce')
            
            print(">>> [Data] Scrip Master Loaded.")
        except Exception as e:
            print(f">>> [Error] Failed to load Scrip Master: {e}")

    def get_active_nifty_expiry(self) -> str:
        """Finds the nearest upcoming or today's valid NIFTY option expiry directly from the scrip master."""
        if self.df is None:
            self.load_scrip_master()

        try:
            nifty = self.df[(self.df['name'] == 'NIFTY') & (self.df['instrumenttype'] == 'OPTIDX')]
            if nifty.empty:
                return "09NOV2026"

            exp_series = pd.to_datetime(nifty['expiry'], format="%d%b%Y", errors='coerce').dropna()
            today = pd.to_datetime(datetime.date.today())
            valid = exp_series[exp_series >= today].sort_values()
            if not valid.empty:
                return valid.iloc[0].strftime("%d%b%Y").upper()
        except Exception as e:
            print(f">>> [Error] get_active_nifty_expiry failed: {e}")

        return "09NOV2026"

    def get_token(self, symbol_name, expiry_date, strike, option_type):
        """
        Finds token for NIFTY Options.
        expiry_date: '29JAN2026'
        strike: 23000
        option_type: 'CE' or 'PE'
        """
        if self.df is None:
            self.load_scrip_master()

        # Input strike is normal (e.g. 23000). Convert to Paise (2300000)
        strike_paise = float(strike) * 100.0
        
        # Filter Logic
        row = self.df[
            (self.df['name'] == 'NIFTY') & 
            (self.df['instrumenttype'] == 'OPTIDX') & 
            (self.df['strike'] == strike_paise) &
            (self.df['symbol'].str.endswith(option_type)) &
            (self.df['expiry'] == expiry_date)
        ]

        if not row.empty:
            return row.iloc[0]['token'], row.iloc[0]['symbol']
        return None, None
