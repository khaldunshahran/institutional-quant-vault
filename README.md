# Institutional Quant Vault (TypeSafe Jev)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-active-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)]()

Institutional-grade quantitative futures trading system operating across **Gold (XAU/PAXG), Bitcoin (BTC), and Binance Liquid Mega-Caps (ETH, SOL, AVAX, LINK, DOGE, XRP, LTC)**.

Designed around a **$100,000.00 USD Institutional Quant Vault** executing strict mathematical **"Profit or Fee-Protected Break-Even"** mechanics.

---

## 🏛️ System Architecture

```
                                  ┌───────────────────────────┐
                                  │   Binance Live Market     │
                                  │  (Futures & Spot Streams) │
                                  └─────────────┬─────────────┘
                                                │
                 ┌──────────────────────────────┼──────────────────────────────┐
                 ▼                              ▼                              ▼
      ┌────────────────────┐         ┌────────────────────┐         ┌────────────────────┐
      │  Order Book L2     │         │  Order Flow (CVD)  │         │  Session Clock     │
      │  • Multi-Asset OBI │         │  • USD Notional    │         │  • NY Cash / London│
      │  • Whale Walls     │         │  • Absorption/Trap │         │  • Liq Magnet Pools│
      └──────────┬─────────┘         └──────────┬─────────┘         └──────────┬─────────┘
                 │                              │                              │
                 └──────────────────────────────┼──────────────────────────────┘
                                                ▼
                                  ┌───────────────────────────┐
                                  │   TypeSafe Jev AI Brain   │
                                  │  (Hurst, MAD, Kelly, O-U) │
                                  └─────────────┬─────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │  Autonomous Quant Vault   │
                                  │  • Up to 15 Positions     │
                                  │  • $10K Notional / 10x    │
                                  │  • Early Break-Even       │
                                  │  • Dynamic TP Trailing    │
                                  └──────┬─────────────┬──────┘
                                         │             │
                    ┌────────────────────┘             └────────────────────┐
                    ▼                                                       ▼
      ┌───────────────────────────┐                           ┌───────────────────────────┐
      │  Cockpit UI (Port 5000)   │                           │  Telegram 2-Way Command   │
      │  • Real-time Radar        │                           │  • Live Trade Alerts      │
      │  • Monte Carlo Sim        │                           │  • Remote Status Polling  │
      └───────────────────────────┘                           └───────────────────────────┘
```

---

## 🚀 Key Modules & Capabilities

### 1. Autonomous Multi-Asset Trader (`scripts/autonomous_multi_asset_trader.py`)
- **Bankroll**: $100,000.00 USD paper/live capital.
- **Position Sizing**: $10,000 notional per trade ($1,000 margin @ 10x leverage).
- **Concurrent Capacity**: Up to 15 simultaneous uncorrelated positions.
- **Early Break-Even Ratchet**: Stop loss automatically moved to fee-protected break-even (+0.03% buffer) once trade advances +1.0 ATR (+0.60%).
- **Fast TP1 Scale-Out**: Banks 50% profit at +0.80%–1.20% and guarantees zero risk for remainder.
- **Trailing Runner**: Dynamic trailing stops on remaining 50% position.

### 2. Multi-Symbol L2 Order Book Engine (`scripts/order_book_engine.py`)
- Computes real-time Order Book Imbalance (OBI) across the top 50 bid/ask levels.
- Fully isolated multi-symbol evaluation with localized caching (SOL, ETH, BTC, Gold, DOGE).
- Detects institutional whale limit walls calibrated by asset market cap ($10M BTC, $4M ETH, $2M Gold, $1M liquid altcoins).

### 3. Scale-Invariant Order Flow CVD Engine (`scripts/order_flow_engine.py`)
- Calculates 5M and 15M Cumulative Volume Delta in quote asset (USDT notional).
- Computes `delta_share_pct` and `taker_ratio_15m` for scale-invariant order flow analysis across all asset classes.
- Detects institutional **Bullish Absorption** (bids soaking sell pressure) and **Bearish Exhaustion** (chasing pumps into liquidity).

### 4. Institutional Session Clock & Liquidity Magnets (`scripts/session_clock_engine.py`)
- Maps global UTC regimes: New York Cash Open (13:30–16:30 UTC), London Sweeps (07:00–10:30 UTC), Asia Range consolidation (00:00–06:00 UTC).
- Projects short-squeeze and long-liquidation magnet pools around 24h highs and lows with adaptive decimal precision.

### 5. Mathematical Quant Engine (`scripts/math_quant_engine.py`)
- **Hurst Exponent ($H$)**: Multi-lag R/S regression classifying trending momentum ($H \ge 0.56$), mean-reverting chop ($H \le 0.44$), or random walk.
- **Robust MAD Z-Score**: Outlier detection via Median Absolute Deviation ($1.4826 \times \text{MAD}$).
- **Ornstein-Uhlenbeck Process**: Calculates continuous mean-reversion half-life ($\tau = \ln(2)/\theta$).
- **Fractional Kelly Criterion**: Computes mathematically optimal position sizing.

### 6. Monte Carlo 10,000-Run Strategy Simulator (`scripts/monte_carlo_engine.py`)
- Simulates 10,000 stochastic 30-day trading paths.
- Calibrated to the $100K Vault: +$2,000.00 daily profit target (+2.0%/day) and -$2,000.00 daily circuit breaker.
- Demonstrates 77%+ probability of achieving daily profit targets with less than 5% maximum drawdown.

---

## 🛠️ Quick Start

### Prerequisites
- Python 3.10+
- Virtual environment with dependencies:
```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### Running Tests
Execute the comprehensive test suite (19 unit & integration tests):
```bash
pytest tests/ -v
```

### Launching the Dashboard & Autopilot
```bash
python ui/server.py --port 5000
```
Open **`http://localhost:5000/`** to view the live radar, order book depth, CVD telemetry, active positions, and Monte Carlo simulator.

---

## 📂 Repository Layout
```
├── archive/
│   └── polymarket_legacy/        # Preserved legacy 5m binary betting scripts
├── config/                       # Strategy profiles & thresholds
├── runtime/                      # Epistemic trade history & active state (git-ignored)
├── scripts/
│   ├── autonomous_multi_asset_trader.py  # Main $100K Quant Vault Daemon
│   ├── binance_execution_adapter.py      # Binance Futures execution layer
│   ├── binance_universe_scanner.py       # Multi-asset parallel scanner
│   ├── economic_calendar_engine.py       # Tier-1 news blackout shield
│   ├── episodic_memory_engine.py         # Continuous post-mortem learning
│   ├── jev_decision_engine.py            # AI conviction & setup validator
│   ├── math_quant_engine.py              # Hurst, MAD, Kelly, O-U math
│   ├── monte_carlo_engine.py             # 10,000-run stochastic stress tester
│   ├── order_book_engine.py              # L2 Depth & whale wall detector
│   ├── order_flow_engine.py              # Real-time Spot & Perp CVD
│   ├── session_clock_engine.py           # Liquidity magnet pool engine
│   └── telegram_alert_bot.py             # Two-way remote telegram interface
├── tests/                                # 100% passing pytest suite
├── ui/                                   # Real-time WebSocket/REST Cockpit
└── README.md
```

---

## 📜 Historical Evolution
This codebase originated as an experimental 5-minute binary options algorithm for Polymarket. It has been completely re-architected into a multi-asset quantitative futures engine operating directly on Binance institutional liquidity. Legacy Polymarket components are archived in [`archive/polymarket_legacy/`](archive/polymarket_legacy/) for historical reference.
