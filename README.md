# T-Agent: Institutional Algorithmic Trading System

A multi-layered algorithmic trading agent system built for Nifty 50 Options and multi-asset trading, conforming to **T-Agent BRD V1.3**.

---

## 🚀 Key Features

* **Smart Money Concepts (SMC) Strategy (`--strategy SMC`):**
  * Equal Highs (EQH) & Equal Lows (EQL) liquidity pool detection with ATR tolerance (`|High1 - High2| <= ATR * 0.2`).
  * Multi-timeframe trend alignment (H1 bias gates M5 entry precision).
  * Institutional trap / fake breakout filter (wick-to-body ratio > 1.5 with below-average volume).
  * Strict minimum 1:2 Risk-Reward ratio enforcement.
* **Momentum Strategy (`--strategy MOMENTUM`):** 9/21 EMA crossover on 5-minute candles with RSI filters (<70 for CE, >30 for PE).
* **Dynamic 9:20 Straddle (`--strategy STRADDLE`):** Automatic ATM strike selection and dual-leg shorting with 25% premium stops.
* **Institutional Risk Controls & Gatekeeper:**
  * **2% Daily Loss Kill Switch:** Halts all trading and closes positions if realized + unrealized loss hits the 2% cap.
  * **Volatility-Adjusted Sizing:** Automatically cuts lot size by 50% when India VIX > 25.
  * **Margin Gatekeeper:** Requires a 10% safety buffer over broker margin requirements.
* **Superhuman Trailing Stop Loss & Breakeven Engine:**
  * Moves SL to breakeven (risk-free) once +10% profit is achieved.
  * Step-ladder dynamic trailing stops trail 10% below peak profit.
* **ACID State Persistence & Audit Logging:**
  * Conforming to BRD Section 6 schema (`trades`, `signals`, `sl_modifications`, `audit_logs`).
  * Production ready with PostgreSQL 15+ and automatic local SQLite fallback (`data/trading_agent.db`).
* **Proactive Broker Session Management:**
  * Proactive token renewal 10 minutes prior to expiry.
  * Auto-relogin with exponential backoff on connection drops.
  * Token-bucket rate limiter eliminating blocking sleep delays.
* **Real-time Telemetry Dashboard:** View open positions, daily P&L, signals, and audit events.
* **Quantitative Backtesting Engine:** Computes Sharpe Ratio, Sortino Ratio, Maximum Drawdown, Win Rate, and Profit Factor.

---

## 🛠️ Setup & Installation

1. **Activate the Virtual Environment:**
   ```bash
   # Windows
   .\.venv\Scripts\activate
   # Linux / macOS
   source .venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment (`.env`):**
   ```ini
   # Angel One SmartAPI Credentials
   API_KEY=your_api_key
   CLIENT_ID=your_client_id
   PASSWORD=your_password
   TOTP_SECRET=your_totp_secret

   # Database (Optional - defaults to local SQLite at data/trading_agent.db)
   DATABASE_URL=postgresql://user:password@localhost:5432/trading_agent
   ```

---

## 🏃 Usage Guide

### 1. Mock Testing Mode (Safe, No Broker Credentials Needed)
```bash
python main.py --test --strategy SMC
python main.py --test --strategy STRADDLE
python main.py --test --strategy MOMENTUM
```

### 2. Real-time Telemetry Dashboard
View live positions, realized daily P&L, generated signals, and audit trails:
```bash
python tools/telemetry_dashboard.py
```

### 3. Dry Run Mode (Real Market Data, Simulated Orders)
```bash
python main.py --dry-run --strategy SMC
```

### 4. Smart Auto-Mode (AI Selection)
```bash
python main.py --auto
```

### 5. Running the Test Suite
Run the 16 automated unit and integration tests:
```bash
python -m unittest discover tests
```

---

## 📂 Project Architecture

```text
├── config/
│   └── settings.py          # Configuration and secrets management
├── core/
│   ├── angel_connect.py     # Angel One SmartAPI connector
│   ├── session_manager.py   # Token lifetime & proactive renewal daemon
│   ├── safety_checks.py     # Gatekeeper: Kill switch, RR filter, VIX sizing
│   ├── position_manager.py  # Trailing SL & Breakeven lifecycle manager
│   ├── data_fetcher.py      # Resilient candle data fetching
│   ├── decision_engine.py   # Strategy routing logic
│   ├── backtester.py        # Institutional backtest & metrics calculator
│   └── mock_connect.py      # Mock broker for local simulation
├── db/
│   ├── models.py            # SQLAlchemy schema (Trades, Signals, Audits, SL Mods)
│   ├── database.py          # PostgreSQL & SQLite session engine
│   └── repository.py        # Data access layer for trade lifecycle
├── strategies/
│   ├── smc_liquidity_strategy.py # Smart Money Concepts (EQH/EQL sweeps, MTF)
│   ├── momentum_strategy.py      # EMA Crossover + RSI strategy
│   ├── nifty_straddle.py         # 9:20 Intraday Straddle
│   ├── orb_strategy.py           # Opening Range Breakout
│   ├── vwap_strategy.py          # Institutional VWAP trend
│   ├── ohl_strategy.py           # Open High Low scalping
│   └── inside_bar_strategy.py    # Inside bar breakout
├── tools/
│   └── telemetry_dashboard.py    # Real-time CLI telemetry monitor
├── utils/
│   ├── logger.py            # Thread-safe rotating logger
│   ├── rate_limiter.py      # Token-bucket rate limiter (<100ms floor)
│   ├── expiry_calculator.py # Target expiry calculation
│   └── token_lookup.py      # Scrip master search
├── tests/                   # 16 unit and integration test suites
├── main.py                  # CLI application entry point
└── requirements.txt         # Dependencies
```

---

## ⚠️ Disclaimer
Algorithmic trading involves significant financial risk. Always test strategies rigorously in Mock Mode before deploying live capital.
