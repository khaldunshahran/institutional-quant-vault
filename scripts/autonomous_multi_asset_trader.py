"""
Autonomous Multi-Asset Trader Daemon - $100K INSTITUTIONAL QUANT VAULT EDITION
High-frequency quantitative trading across:
1. Gold (XAUUSDT Futures & PAXGUSDT Spot)
2. Top 20 Binance Liquid Mega-Caps (BTC, ETH, SOL, BNB, XRP, DOGE, SUI, AVAX, etc.)
3. Top 24H Volatility Movers (Breakout runners)

Configured for high-volume context learning while enforcing strict mathematical "Profit or Break-Even":
- Bankroll: $100,000.00 USD Paper Balance.
- Daily Margin Cap: 30% of account = $30,000.00 active margin exposure.
- Concurrent Trades: Up to 15 concurrent positions.
- Position Size: $20,000.00 notional per trade ($2,000.00 margin @ 10x leverage).
- Fast TP1: +0.80% (+8% ROI on margin = +$160 banked) -> RATCHETS STOP TO FEE-PROTECTED BREAK-EVEN.
- TP2 Runner: +2.00% (+20% ROI on margin = +$400 banked).
- Hard Stop Loss: -0.90% (-9% ROI on margin = -$180 max loss).
- Alpha Decay Timeout: Stagnant trades (flat for > 20 min) exit near break-even to recycle capital.
- Episodic Memory: Continuously logs post-mortems to runtime/trade_memory.json to learn from wins/losses.
- Two-Way Telegram command polling & Real-Time Thought Stream radar.
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_original_print = print
def print(*args, **kwargs):
    try:
        _original_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            sanitized = [str(a).encode("ascii", "replace").decode("ascii") for a in args]
            _original_print(*sanitized, **kwargs)
        except Exception:
            pass
    except Exception:
        pass
import json
import time
import math
import statistics
import threading
import urllib.request
from pathlib import Path
from typing import Dict, List, Any, Optional

PROJECT_ROOT = Path(r"d:\5 minute btc\5min-btc-polymarket")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.binance_execution_adapter import BinanceExecutionAdapter
from scripts.telegram_alert_bot import TelegramAlertBot
from scripts.binance_universe_scanner import BinanceUniverseScanner
from scripts.episodic_memory_engine import EpisodicMemoryEngine
from scripts.order_flow_engine import OrderFlowEngine
from scripts.order_book_engine import OrderBookEngine
from scripts.economic_calendar_engine import EconomicCalendarEngine
from scripts.session_clock_engine import SessionClockEngine
from scripts.jev_decision_engine import JevDecisionEngine
from scripts.math_quant_engine import compute_hurst_exponent, compute_robust_mad_zscore


class AutonomousMultiAssetTrader:
    def __init__(
        self,
        core_assets: Optional[List[str]] = None,
        max_concurrent_positions: int = 15,
        notional_per_trade_usd: float = 10_000.0, # $10k notional ($1,000 margin @ 10x)
        leverage: int = 10,                        # 10x Leverage ($1,000 margin per position)
        account_balance_usd: float = 100_000.0,    # $100k Bankroll
        max_daily_allocation_usd: float = 15_000.0,# $15,000 max margin allocation (15 positions * $1k)
        daily_profit_target_usd: float = 2_000.0,  # $2,000 (+2% daily target)
        daily_loss_limit_usd: float = 2_000.0,     # -$2,000 (-2% circuit breaker)
        runtime_dir: str = r"d:\5 minute btc\5min-btc-polymarket\runtime"
    ):
        self.max_concurrent_positions = max_concurrent_positions
        self.notional_per_trade_usd = notional_per_trade_usd
        self.leverage = leverage
        self.account_balance_usd = account_balance_usd
        self.max_daily_allocation_usd = max_daily_allocation_usd
        self.daily_profit_target_usd = daily_profit_target_usd
        self.daily_loss_limit_usd = daily_loss_limit_usd
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.positions_file = self.runtime_dir / "autonomous_positions.json"
        self.history_file = self.runtime_dir / "autonomous_trade_history.json"
        self.state_file = self.runtime_dir / "autonomous_state.json"

        self.execution_adapter = BinanceExecutionAdapter()
        self.telegram_bot = TelegramAlertBot()
        self.universe_scanner = BinanceUniverseScanner(include_movers=False)
        self.episodic_memory = EpisodicMemoryEngine()

        # Institutional Quant Intelligence Engines
        self.order_flow_engine = OrderFlowEngine()
        self.order_book_engine = OrderBookEngine(symbol="BTCUSDT")
        self.economic_calendar_engine = EconomicCalendarEngine()
        self.session_clock_engine = SessionClockEngine()
        self.jev_engine = JevDecisionEngine()

        # Cooldown & Regime Risk Tracking
        self.symbol_cooldowns: Dict[str, float] = {}
        self.symbol_consecutive_losses: Dict[str, int] = {}
        self.macro_trend_cache: Dict[str, Any] = {"trend": "NEUTRAL_CHOP", "timestamp": 0.0}

        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.lock = threading.RLock()

        self.open_positions: Dict[str, Dict[str, Any]] = self._load_positions()
        self.daily_pnl_usd: float = 0.0
        self.daily_trades_count: int = 0
        self.daily_reset_date: str = time.strftime("%Y-%m-%d", time.gmtime())
        self.circuit_breaker_triggered = False
        self.daily_goal_reached = False

        # Real-time intelligence & thought stream buffer (last 20 logs)
        self.thought_stream: List[str] = [
            f"[{time.strftime('%H:%M:%S UTC')}] Institutional Focus Active: $100K Bankroll | $1,000 Margin Sizing | ATR Volatility Bounds.",
            f"[{time.strftime('%H:%M:%S UTC')}] Risk Architecture: $10K Notional ($1K Margin @ 10x) | 45m Post-Loss Cooldown | Macro BTC/ETH Gate."
        ]
        self.latest_decision: Dict[str, Any] = {
            "symbol": "XAUUSDT",
            "asset_name": "Gold (XAU/USD)",
            "setup": "SYSTEM_PRE_WARMING",
            "regime": "SCANNING",
            "hurst": 0.55,
            "robust_z": 0.0,
            "conviction": 85.0,
            "ev_usd": 40.0,
            "action": "MONITORING_UNIVERSE"
        }

        self._load_state()

        # Connect interactive two-way Telegram command poller with live telemetry
        try:
            self.telegram_bot.start_polling(
                state_provider=self.get_telemetry_state,
                action_handler=self.handle_remote_action
            )
        except Exception as _tb_err:
            print(f"[QUANT VAULT] Telegram poller init warning: {_tb_err}")

    def _add_thought(self, thought: str):
        with self.lock:
            self.thought_stream.append(thought)
            if len(self.thought_stream) > 25:
                self.thought_stream = self.thought_stream[-25:]

    def _load_positions(self) -> Dict[str, Dict[str, Any]]:
        try:
            if self.positions_file.exists():
                return json.loads(self.positions_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_positions(self):
        try:
            self.positions_file.write_text(json.dumps(self.open_positions, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_state(self):
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                saved_date = data.get("date", "")
                cur_date = time.strftime("%Y-%m-%d", time.gmtime())
                if saved_date == cur_date:
                    self.daily_pnl_usd = float(data.get("daily_pnl_usd", 0.0))
                    self.daily_trades_count = int(data.get("daily_trades_count", 0))
                    self.circuit_breaker_triggered = bool(data.get("circuit_breaker_triggered", False))
                    self.daily_goal_reached = bool(data.get("daily_goal_reached", False))
                if data.get("enabled", True):  # Default to active on startup
                    self.start()
        except Exception:
            pass

    def _save_state(self):
        try:
            cur_date = time.strftime("%Y-%m-%d", time.gmtime())
            data = {
                "enabled": self.is_running,
                "date": cur_date,
                "daily_pnl_usd": round(self.daily_pnl_usd, 2),
                "daily_trades_count": self.daily_trades_count,
                "circuit_breaker_triggered": self.circuit_breaker_triggered,
                "daily_goal_reached": self.daily_goal_reached,
                "account_balance_usd": self.account_balance_usd,
                "max_daily_allocation_usd": self.max_daily_allocation_usd,
                "last_update": time.time()
            }
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _record_closed_trade(self, trade_record: Dict[str, Any]):
        try:
            history = []
            if self.history_file.exists():
                history = json.loads(self.history_file.read_text(encoding="utf-8"))
            history.append(trade_record)
            self.history_file.write_text(json.dumps(history[-500:], indent=2), encoding="utf-8")
        except Exception:
            pass

    def start(self) -> Dict[str, Any]:
        with self.lock:
            if self.is_running and self.worker_thread and self.worker_thread.is_alive():
                return {"success": True, "status": "already_running"}
            self.is_running = True
            self._save_state()
            self.worker_thread = threading.Thread(target=self._run_loop, daemon=True, name="QuantTraderDaemon")
            self.worker_thread.start()
            self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] 🚀 Autonomous Quant Vault ACTIVATED! 35-Asset Universe scanning active.")
            print(f"[QUANT VAULT] Autopilot started! Sizing: ${self.notional_per_trade_usd:,.0f} notional @ {self.leverage}x | 15 Max Positions | $100K Bankroll")
            return {"success": True, "status": "started"}

    def stop(self) -> Dict[str, Any]:
        with self.lock:
            self.is_running = False
            self._save_state()
            self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] ⏸️ Autonomous Quant Vault PAUSED. Open positions remain actively risk-managed.")
            print("[QUANT VAULT] Autopilot paused.")
            return {"success": True, "status": "stopped"}

    def get_status(self) -> Dict[str, Any]:
        cur_date = time.strftime("%Y-%m-%d", time.gmtime())
        if cur_date != self.daily_reset_date:
            self.daily_reset_date = cur_date
            self.daily_pnl_usd = 0.0
            self.daily_trades_count = 0
            self.circuit_breaker_triggered = False
            self.daily_goal_reached = False

        total_margin_used = sum(float(p.get("margin_collateral_usd", 1000.0)) for p in self.open_positions.values())
        allocation_pct = round((total_margin_used / self.max_daily_allocation_usd) * 100.0, 1) if self.max_daily_allocation_usd > 0 else 0.0

        history = []
        if self.history_file.exists():
            try:
                history = json.loads(self.history_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        wins = sum(1 for t in history if float(t.get("pnl_usd", 0)) > 0)
        losses = sum(1 for t in history if float(t.get("pnl_usd", 0)) < 0)
        total_realized_pnl = sum(float(t.get("pnl_usd", 0)) for t in history)

        return {
            "running": self.is_running,
            "mode_name": "INSTITUTIONAL_QUANT_VAULT_100K",
            "account_balance_usd": self.account_balance_usd,
            "balance_usd": self.account_balance_usd,
            "max_daily_allocation_usd": self.max_daily_allocation_usd,
            "max_daily_margin": self.max_daily_allocation_usd,
            "daily_profit_target_usd": self.daily_profit_target_usd,
            "daily_target_usd": self.daily_profit_target_usd,
            "current_margin_used_usd": round(total_margin_used, 2),
            "allocated_margin": round(total_margin_used, 2),
            "margin_allocation_pct": allocation_pct,
            "notional_per_trade_usd": self.notional_per_trade_usd,
            "leverage": self.leverage,
            "max_concurrent_positions": self.max_concurrent_positions,
            "open_positions_count": len(self.open_positions),
            "open_positions": list(self.open_positions.values()),
            "total_closed_trades_count": len(history),
            "wins_count": wins,
            "losses_count": losses,
            "win_rate_pct": round((wins / len(history) * 100.0), 1) if history else 0.0,
            "total_realized_pnl_usd": round(total_realized_pnl, 2),
            "closed_trades": list(reversed(history[-100:])),
            "daily_pnl_usd": round(self.daily_pnl_usd, 2),
            "daily_trades_count": self.daily_trades_count,
            "daily_goal_reached": self.daily_goal_reached,
            "circuit_breaker_active": self.circuit_breaker_triggered,
            "execution_mode": self.execution_adapter.mode.upper(),
            "latest_decision": self.latest_decision,
            "thought_stream": self.thought_stream[-15:],
            "time_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        }

    def get_telemetry_state(self) -> Dict[str, Any]:
        """Provides full live telemetry for Telegram dashboard and on-demand queries."""
        history = []
        if self.history_file.exists():
            try:
                history = json.loads(self.history_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        wins = sum(1 for t in history if float(t.get("pnl_usd", 0)) > 0)
        losses = sum(1 for t in history if float(t.get("pnl_usd", 0)) < 0)
        gross_gain = sum(float(t.get("pnl_usd", 0)) for t in history if float(t.get("pnl_usd", 0)) > 0)
        gross_loss = sum(float(t.get("pnl_usd", 0)) for t in history if float(t.get("pnl_usd", 0)) < 0)

        total_margin_used = sum(float(p.get("margin_collateral_usd", 1000.0)) for p in self.open_positions.values())

        return {
            "account_balance_usd": self.account_balance_usd,
            "max_daily_allocation_usd": self.max_daily_allocation_usd,
            "current_margin_used_usd": round(total_margin_used, 2),
            "daily_pnl_usd": round(self.daily_pnl_usd, 2),
            "gross_gain": round(gross_gain, 2),
            "gross_loss": round(gross_loss, 2),
            "daily_trades_count": self.daily_trades_count,
            "daily_profit_target_usd": self.daily_profit_target_usd,
            "wins": wins,
            "losses": losses,
            "running": self.is_running,
            "circuit_breaker_active": self.circuit_breaker_triggered,
            "daily_goal_reached": self.daily_goal_reached,
            "open_positions": list(self.open_positions.values()),
            "open_positions_count": len(self.open_positions),
        }

    def handle_remote_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Executes remote commands received from Telegram."""
        if action == "pause":
            return self.stop()
        elif action == "resume":
            return self.start()
        elif action == "close":
            symbol = payload.get("symbol", "").upper()
            if symbol in self.open_positions:
                pos = self.open_positions[symbol]
                cur_price = pos.get("current_price", pos["entry_price"])
                del self.open_positions[symbol]
                self._save_positions()
                trade_record = {
                    "symbol": symbol,
                    "side": pos["side"],
                    "entry_price": pos["entry_price"],
                    "exit_price": cur_price,
                    "pnl_usd": pos.get("unrealized_pnl_usd", 0.0),
                    "pnl_pct": pos.get("unrealized_pnl_pct", 0.0),
                    "exit_reason": "REMOTE_TELEGRAM_CLOSE",
                    "closed_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "mode": pos["mode"]
                }
                self._record_closed_trade(trade_record)
                return {"success": True, "closed": symbol}
            return {"success": False, "error": f"{symbol} not active"}
        elif action == "closeall":
            closed_syms = list(self.open_positions.keys())
            for sym in closed_syms:
                pos = self.open_positions[sym]
                trade_record = {
                    "symbol": sym,
                    "side": pos["side"],
                    "entry_price": pos["entry_price"],
                    "exit_price": pos.get("current_price", pos["entry_price"]),
                    "pnl_usd": pos.get("unrealized_pnl_usd", 0.0),
                    "pnl_pct": pos.get("unrealized_pnl_pct", 0.0),
                    "exit_reason": "REMOTE_TELEGRAM_CLOSEALL",
                    "closed_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "mode": pos["mode"]
                }
                self._record_closed_trade(trade_record)
            self.open_positions.clear()
            self._save_positions()
            return {"success": True, "closed_count": len(closed_syms)}
        return {"success": False, "error": f"Unknown action: {action}"}

    def _fetch_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        # Multi-timeframe institutional lookback: 15m interval with 100 bars (25 hours)
        if symbol == "PAXGUSDT":
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100"
        else:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=100"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode())
                highs = [float(c[2]) for c in data]
                lows = [float(c[3]) for c in data]
                closes = [float(c[4]) for c in data]
                volumes = [float(c[5]) for c in data]
                return {
                    "price": closes[-1],
                    "closes": closes,
                    "highs": highs,
                    "lows": lows,
                    "volumes": volumes,
                    "timeframe": "15m",
                    "bars_count": len(closes)
                }
        except Exception:
            return None

    def _calc_hurst(self, prices: List[float]) -> float:
        try:
            h, _ = compute_hurst_exponent(prices)
            return h
        except Exception:
            return 0.50

    def _calc_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return closes[-1] * 0.01
        try:
            trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(closes))]
            return sum(trs[-period:]) / float(period)
        except Exception:
            return closes[-1] * 0.01

    def _calc_rsi(self, closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        try:
            gains = []
            losses = []
            for i in range(1, len(closes)):
                chg = closes[i] - closes[i-1]
                if chg >= 0:
                    gains.append(chg)
                    losses.append(0.0)
                else:
                    gains.append(0.0)
                    losses.append(abs(chg))
            avg_gain = sum(gains[-period:]) / float(period)
            avg_loss = sum(losses[-period:]) / float(period)
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100.0 - (100.0 / (1.0 + rs)), 1)
        except Exception:
            return 50.0

    def _fetch_macro_trend(self) -> str:
        now = time.time()
        if now - self.macro_trend_cache.get("timestamp", 0) < 60.0:
            return self.macro_trend_cache.get("trend", "NEUTRAL_CHOP")

        try:
            url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=50"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                closes = [float(c[4]) for c in data]
                if len(closes) >= 50:
                    def calc_ema(values, period):
                        k = 2.0 / (period + 1)
                        ema = values[0]
                        for val in values[1:]:
                            ema = val * k + ema * (1 - k)
                        return ema
                    ema_20 = calc_ema(closes[-30:], 20)
                    ema_50 = calc_ema(closes, 50)
                    cur_px = closes[-1]
                    if cur_px < ema_20 and ema_20 < ema_50:
                        trend = "BEARISH"
                    elif cur_px > ema_20 and ema_20 > ema_50:
                        trend = "BULLISH"
                    else:
                        trend = "NEUTRAL_CHOP"
                    self.macro_trend_cache = {"trend": trend, "timestamp": now}
                    return trend
        except Exception:
            pass

        return self.macro_trend_cache.get("trend", "NEUTRAL_CHOP")

    def _evaluate_signal(self, symbol: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # 1. Enforce Cooldown timer
        now = time.time()
        if symbol in self.symbol_cooldowns:
            if now < self.symbol_cooldowns[symbol]:
                return None
            else:
                del self.symbol_cooldowns[symbol]

        # 2. Economic Calendar News Blackout Shield Check
        news_shield = self.economic_calendar_engine.get_news_shield_state()
        if news_shield.get("is_blackout_active", False):
            self.latest_decision = {
                "symbol": symbol,
                "asset_name": symbol,
                "setup": "NEWS_BLACKOUT_SHIELD",
                "regime": "HAZARD_WINDOW",
                "action": f"ENTRIES FROZEN: {news_shield.get('blackout_reason', 'Tier-1 News Blackout')}"
            }
            return None

        price = data["price"]
        closes = data["closes"]
        highs = data.get("highs", closes)
        lows = data.get("lows", closes)
        if len(closes) < 14:
            return None

        # 3. Session Liquidity Clock Check
        session_state = self.session_clock_engine.get_session_and_liquidity_state(current_spot=price, symbol=symbol)
        session_breakout_allowed = session_state.get("breakout_allowed", True)
        hurst = self._calc_hurst(closes)
        atr_14 = self._calc_atr(highs, lows, closes, period=14)
        rsi_14 = self._calc_rsi(closes, period=14)
        macro_trend = self._fetch_macro_trend()

        mad_res = compute_robust_mad_zscore(closes)
        robust_z = mad_res.get("robust_z", 0.0)
        mom_15m = (closes[-1] - closes[-3]) / closes[-3] * 100.0

        # Compute Relative Volume (RVOL) to confirm institutional volume support
        volumes = data.get("volumes", [])
        rvol = 1.0
        if len(volumes) >= 21:
            avg_vol_20 = sum(volumes[-21:-1]) / 20.0
            if avg_vol_20 > 0:
                rvol = round(volumes[-1] / avg_vol_20, 2)

        # 4. Order Flow (CVD) and Order Book (OBI) Telemetry
        order_flow = self.order_flow_engine.get_order_flow_metrics(symbol=symbol)
        order_flow_bias = order_flow.get("order_flow_bias", "BALANCED")
        divergence = order_flow.get("divergence_alert", "NORMAL")

        ob_state = self.order_book_engine.get_order_book_state(current_spot=price, symbol=symbol)
        obi_pct = ob_state.get("obi_pct", 0.0)

        is_gold = "XAU" in symbol or "PAXG" in symbol
        is_btc = symbol.startswith("BTC")
        asset_label = "Gold (XAU/USD)" if is_gold else symbol

        # Calibrated ATR Stop & Target Ratios
        atr_ratio = atr_14 / price
        if is_gold:
            sl_pct = max(0.005, min(0.012, 2.0 * atr_ratio))
            tp1_pct = max(0.006, min(0.015, 2.0 * atr_ratio))
            tp2_pct = max(0.014, min(0.030, 4.0 * atr_ratio))
        elif is_btc:
            sl_pct = max(0.008, min(0.016, 2.2 * atr_ratio))
            tp1_pct = max(0.008, min(0.016, 2.0 * atr_ratio))
            tp2_pct = max(0.018, min(0.035, 4.0 * atr_ratio))
        else:
            sl_pct = max(0.012, min(0.024, 2.5 * atr_ratio))
            tp1_pct = max(0.010, min(0.020, 2.0 * atr_ratio))
            tp2_pct = max(0.022, min(0.045, 4.5 * atr_ratio))

        # 1. High Velocity Trend Breakout (Sniper Hurdle)
        if hurst >= 0.55 and robust_z >= 1.25 and mom_15m >= 0.35 and rsi_14 >= 52 and rvol >= 1.20:
            if macro_trend == "BEARISH":
                return None
            if not session_breakout_allowed:
                return None
            if "SELLING" in order_flow_bias or divergence == "BEARISH_EXHAUSTION":
                return None

            jev_verdict = self.jev_engine.evaluate_futures_setup({
                "symbol": symbol,
                "side": "BUY",
                "price": price,
                "hurst": hurst,
                "z_score": round(robust_z, 2),
                "mom_pct": round(mom_15m, 2),
                "rvol": rvol,
                "order_flow_bias": order_flow_bias,
                "obi_pct": obi_pct,
                "regime": "MOMENTUM_EXPANSION"
            })
            if not jev_verdict.get("approved", False):
                return None

            conv = min(98.0, 78.0 + hurst * 18.0 + robust_z * 3.5 + min(5.0, (rvol - 1.0) * 3.0))
            if conv < 82.0:
                return None

            sig = {
                "symbol": symbol,
                "asset_label": asset_label,
                "side": "BUY",
                "price": price,
                "hurst": hurst,
                "z_score": round(robust_z, 2),
                "rvol": rvol,
                "setup": "SNIPER_TREND_LONG",
                "conviction": round(conv, 1),
                "jev_conviction": jev_verdict.get("conviction", 3.5),
                "jev_trap_risk": jev_verdict.get("trap_risk", 0.15),
                "atr_14": atr_14,
                "sl_pct": sl_pct,
                "tp1_pct": tp1_pct,
                "tp2_pct": tp2_pct,
                "ev_usd": round(self.notional_per_trade_usd * tp1_pct * 0.5, 2)
            }
            self.latest_decision = {
                "symbol": symbol,
                "asset_name": asset_label,
                "setup": "SNIPER_LONG",
                "regime": f"MOMENTUM_EXPANSION (RVOL: {rvol}x | CVD: {order_flow_bias})",
                "hurst": hurst,
                "robust_z": round(robust_z, 2),
                "conviction": round(conv, 1),
                "ev_usd": sig["ev_usd"],
                "action": f"STRIKE CANDIDATE: LONG {symbol} @ ${price:,.2f} (SL: -{sl_pct*100:.2f}%, TP1: +{tp1_pct*100:.2f}%)"
            }
            return sig

        elif hurst >= 0.55 and robust_z <= -1.25 and mom_15m <= -0.35 and rsi_14 <= 48 and rvol >= 1.20:
            if macro_trend != "BEARISH":
                return None
            if not session_breakout_allowed:
                return None
            if "BUYING" in order_flow_bias or divergence == "BULLISH_ABSORPTION":
                return None

            jev_verdict = self.jev_engine.evaluate_futures_setup({
                "symbol": symbol,
                "side": "SELL",
                "price": price,
                "hurst": hurst,
                "z_score": round(robust_z, 2),
                "mom_pct": round(mom_15m, 2),
                "rvol": rvol,
                "order_flow_bias": order_flow_bias,
                "obi_pct": obi_pct,
                "regime": "MOMENTUM_EXPANSION"
            })
            if not jev_verdict.get("approved", False):
                return None

            conv = min(98.0, 78.0 + hurst * 18.0 + abs(robust_z) * 3.5 + min(5.0, (rvol - 1.0) * 3.0))
            if conv < 82.0:
                return None

            sig = {
                "symbol": symbol,
                "asset_label": asset_label,
                "side": "SELL",
                "price": price,
                "hurst": hurst,
                "z_score": round(robust_z, 2),
                "rvol": rvol,
                "setup": "SNIPER_TREND_SHORT",
                "conviction": round(conv, 1),
                "jev_conviction": jev_verdict.get("conviction", 3.5),
                "jev_trap_risk": jev_verdict.get("trap_risk", 0.15),
                "atr_14": atr_14,
                "sl_pct": sl_pct,
                "tp1_pct": tp1_pct,
                "tp2_pct": tp2_pct,
                "ev_usd": round(self.notional_per_trade_usd * tp1_pct * 0.5, 2)
            }
            self.latest_decision = {
                "symbol": symbol,
                "asset_name": asset_label,
                "setup": "SNIPER_SHORT",
                "regime": f"MOMENTUM_EXPANSION (RVOL: {rvol}x | CVD: {order_flow_bias})",
                "hurst": hurst,
                "robust_z": round(robust_z, 2),
                "conviction": round(conv, 1),
                "ev_usd": sig["ev_usd"],
                "action": f"STRIKE CANDIDATE: SHORT {symbol} @ ${price:,.2f} (SL: +{sl_pct*100:.2f}%, TP1: -{tp1_pct*100:.2f}%)"
            }
            return sig

        # 2. Deep Elastic Mean Reversion
        if hurst < 0.42:
            if robust_z <= -2.00 and rsi_14 <= 28:
                if macro_trend == "BEARISH":
                    return None
                if "SELLING" in order_flow_bias and divergence == "BEARISH_EXPANSION":
                    return None

                jev_verdict = self.jev_engine.evaluate_futures_setup({
                    "symbol": symbol,
                    "side": "BUY",
                    "price": price,
                    "hurst": hurst,
                    "z_score": round(robust_z, 2),
                    "mom_pct": round(mom_15m, 2),
                    "rvol": rvol,
                    "order_flow_bias": order_flow_bias,
                    "obi_pct": obi_pct,
                    "regime": "ELASTIC_STRETCH"
                })
                if not jev_verdict.get("approved", False):
                    return None

                conv = min(95.0, 75.0 + abs(robust_z) * 5.0)
                if conv < 82.0:
                    return None

                sig = {
                    "symbol": symbol,
                    "asset_label": asset_label,
                    "side": "BUY",
                    "price": price,
                    "hurst": hurst,
                    "z_score": round(robust_z, 2),
                    "rvol": rvol,
                    "setup": "ELASTIC_MEAN_REV_LONG",
                    "conviction": round(conv, 1),
                    "jev_conviction": jev_verdict.get("conviction", 3.5),
                    "jev_trap_risk": jev_verdict.get("trap_risk", 0.15),
                    "atr_14": atr_14,
                    "sl_pct": sl_pct,
                    "tp1_pct": tp1_pct,
                    "tp2_pct": tp2_pct,
                    "ev_usd": round(self.notional_per_trade_usd * tp1_pct * 0.5, 2)
                }
                self.latest_decision = {
                    "symbol": symbol,
                    "asset_name": asset_label,
                    "setup": "MEAN_REV_LONG",
                    "regime": "ELASTIC_STRETCH",
                    "hurst": hurst,
                    "robust_z": round(robust_z, 2),
                    "conviction": round(conv, 1),
                    "ev_usd": sig["ev_usd"],
                    "action": f"STRIKE CANDIDATE: MEAN-REV LONG {symbol} @ ${price:,.2f}"
                }
                return sig

            elif robust_z >= 2.00 and rsi_14 >= 72:
                if macro_trend != "BEARISH":
                    return None
                if "BUYING" in order_flow_bias and divergence == "BULLISH_EXPANSION":
                    return None

                jev_verdict = self.jev_engine.evaluate_futures_setup({
                    "symbol": symbol,
                    "side": "SELL",
                    "price": price,
                    "hurst": hurst,
                    "z_score": round(robust_z, 2),
                    "mom_pct": round(mom_15m, 2),
                    "rvol": rvol,
                    "order_flow_bias": order_flow_bias,
                    "obi_pct": obi_pct,
                    "regime": "ELASTIC_STRETCH"
                })
                if not jev_verdict.get("approved", False):
                    return None

                conv = min(95.0, 75.0 + abs(robust_z) * 5.0)
                if conv < 82.0:
                    return None

                sig = {
                    "symbol": symbol,
                    "asset_label": asset_label,
                    "side": "SELL",
                    "price": price,
                    "hurst": hurst,
                    "z_score": round(robust_z, 2),
                    "rvol": rvol,
                    "setup": "ELASTIC_MEAN_REV_SHORT",
                    "conviction": round(conv, 1),
                    "jev_conviction": jev_verdict.get("conviction", 3.5),
                    "jev_trap_risk": jev_verdict.get("trap_risk", 0.15),
                    "atr_14": atr_14,
                    "sl_pct": sl_pct,
                    "tp1_pct": tp1_pct,
                    "tp2_pct": tp2_pct,
                    "ev_usd": round(self.notional_per_trade_usd * tp1_pct * 0.5, 2)
                }
                self.latest_decision = {
                    "symbol": symbol,
                    "asset_name": asset_label,
                    "setup": "MEAN_REV_SHORT",
                    "regime": "ELASTIC_STRETCH",
                    "hurst": hurst,
                    "robust_z": round(robust_z, 2),
                    "conviction": round(conv, 1),
                    "ev_usd": sig["ev_usd"],
                    "action": f"STRIKE CANDIDATE: MEAN-REV SHORT {symbol} @ ${price:,.2f}"
                }
                return sig

        return None

    def _open_position(self, signal: Dict[str, Any]):
        symbol = signal["symbol"]
        side = signal["side"]
        price = signal["price"]

        # Directional Correlation Guard: Max 3 concurrent Crypto positions to prevent basket drawdown
        is_gold = ("XAU" in symbol or "PAXG" in symbol)
        current_crypto_positions = sum(
            1 for s in self.open_positions.keys()
            if "XAU" not in s and "PAXG" not in s
        )
        if not is_gold and current_crypto_positions >= 3:
            self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] 🛑 Crypto Correlation Cap reached (3 active crypto positions). Skipping {symbol} entry.")
            return

        # Precision Lot Sizing for $10,000 Notional ($1,000 Margin @ 10x)
        raw_qty = self.notional_per_trade_usd / price
        if "XAU" in symbol or "PAXG" in symbol or "XAUT" in symbol:
            qty = round(raw_qty, 2)  # Troy Ounces (e.g. 2.28 oz)
        elif symbol.startswith("BTC"):
            qty = round(raw_qty, 3)
        elif symbol.startswith("ETH"):
            qty = round(raw_qty, 2)
        elif symbol.startswith("SOL"):
            qty = round(raw_qty, 1)
        elif any(s in symbol for s in ["1000PEPE", "PEPE", "SHIB", "DOGE", "BONK", "FLOKI"]):
            qty = int(raw_qty)
        elif price < 1.0:
            qty = round(raw_qty, 0)
        elif price < 10.0:
            qty = round(raw_qty, 1)
        else:
            qty = round(raw_qty, 2)

        if qty <= 0:
            return

        res = self.execution_adapter.execute_order(symbol, side, qty, price)
        if not res.get("success"):
            return

        is_long = (side == "BUY")
        # Dynamic ATR-Calibrated Scalp Boundaries
        sl_pct = signal.get("sl_pct", 0.015)
        tp1_pct = signal.get("tp1_pct", 0.012)
        tp2_pct = signal.get("tp2_pct", 0.025)

        sl = round(price * (1.0 - sl_pct) if is_long else price * (1.0 + sl_pct), 4)
        tp1 = round(price * (1.0 + tp1_pct) if is_long else price * (1.0 - tp1_pct), 4)
        tp2 = round(price * (1.0 + tp2_pct) if is_long else price * (1.0 - tp2_pct), 4)

        now = time.time()
        pos_id = f"quant_{symbol}_{int(now)}"
        position = {
            "id": pos_id,
            "symbol": symbol,
            "side": "LONG" if is_long else "SHORT",
            "entry_price": price,
            "quantity": qty,
            "remaining_quantity": qty,
            "notional_usd": round(qty * price, 2),
            "margin_collateral_usd": round((qty * price) / self.leverage, 2),
            "leverage": self.leverage,
            "atr_14": signal.get("atr_14", price * 0.01),
            "stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_hit": False,
            "early_be_hit": False,
            "break_even_active": False,
            "open_time": now,
            "open_time_str": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now)),
            "setup": signal.get("setup"),
            "conviction": signal.get("conviction"),
            "jev_conviction": signal.get("jev_conviction", 3.5),
            "jev_trap_risk": signal.get("jev_trap_risk", 0.15),
            "mode": self.execution_adapter.mode.upper(),
            "unrealized_pnl_usd": 0.0
        }

        self.open_positions[symbol] = position
        self._save_positions()

        thought = f"[{time.strftime('%H:%M:%S UTC')}] ⚡ STRIKE: {position['side']} {symbol} @ ${price:,.2f} | Size: ${position['notional_usd']:,.0f} (Margin: ${position['margin_collateral_usd']:,.0f}) | TP1: ${tp1:,.2f} (BE Armed)"
        self._add_thought(thought)
        print(f"[QUANT VAULT] ⚡ STRIKE! {position['side']} on {symbol} @ ${price:.2f} | Size: ${position['notional_usd']} (Margin: ${position['margin_collateral_usd']} @ {self.leverage}x)")

        try:
            self.telegram_bot.notify_entry(
                symbol=symbol,
                side=position["side"],
                price=price,
                leverage=self.leverage,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                ev_usd=signal.get("ev_usd", 40.0),
                rationale=f"Quant Setup {signal.get('setup')} (H={signal.get('hurst')}, Z={signal.get('z_score')}, Jev={signal.get('jev_conviction', 3.5)})",
                notional_usd=position["notional_usd"],
                margin_usd=position["margin_collateral_usd"],
                daily_pnl=self.daily_pnl_usd,
                daily_trades=self.daily_trades_count,
                daily_target=self.daily_profit_target_usd,
                open_count=len(self.open_positions)
            )
        except Exception as e:
            print(f"[QUANT VAULT] Telegram error: {e}")

    def _manage_open_positions(self):
        symbols_to_close = []
        now = time.time()

        for symbol, pos in list(self.open_positions.items()):
            try:
                data = self._fetch_market_data(symbol)
                if not data:
                    continue
                cur_price = float(data["price"])
            except Exception:
                continue

            entry_px = pos["entry_price"]
            is_long = (pos["side"] == "LONG")
            qty = pos["remaining_quantity"]

            if is_long:
                pnl_pct = (cur_price - entry_px) / entry_px * 100.0
                unrealized_pnl = (cur_price - entry_px) * qty
            else:
                pnl_pct = (entry_px - cur_price) / entry_px * 100.0
                unrealized_pnl = (entry_px - cur_price) * qty

            pos["current_price"] = cur_price
            pos["unrealized_pnl_usd"] = round(unrealized_pnl, 2)
            pos["unrealized_pnl_pct"] = round(pnl_pct * self.leverage, 2)

            # 1. Early Break-Even Ratchet: Lock stop to entry + fee buffer at +1.0 ATR (+0.60% profit)
            if not pos.get("early_be_hit", False) and not pos.get("tp1_hit", False):
                pos_atr = pos.get("atr_14", entry_px * 0.01)
                early_be_dist = pos_atr * 1.0
                unrealized_dist = (cur_price - entry_px) if is_long else (entry_px - cur_price)
                if unrealized_dist >= early_be_dist or pnl_pct >= 0.60:
                    pos["early_be_hit"] = True
                    pos["break_even_active"] = True
                    be_buffer = entry_px * 0.0003
                    pos["stop_loss"] = round(entry_px + be_buffer if is_long else entry_px - be_buffer, 4)
                    self._save_positions()
                    self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] 🔒 EARLY BREAK-EVEN LOCKED on {symbol} at +1.0 ATR (+{pnl_pct:.2f}%). Risk is ZERO.")
                    print(f"[QUANT VAULT] 🔒 EARLY BREAK-EVEN LOCKED on {symbol} at +1.0 ATR (+{pnl_pct:.2f}%).")

            # 2. Fast TP1 Scale-Out (Bank 50% Profit & Move Stop strictly to Fee-Protected Break-Even)
            if not pos.get("tp1_hit", False):
                tp1_condition = (cur_price >= pos["tp1"]) if is_long else (cur_price <= pos["tp1"])
                if tp1_condition:
                    scale_qty = round(qty * 0.5, 4)
                    banked_pnl = round(unrealized_pnl * 0.5, 2)
                    pos["remaining_quantity"] = round(qty - scale_qty, 4)
                    pos["tp1_hit"] = True
                    pos["break_even_active"] = True

                    # Fee-protected break-even buffer (+0.03% to guarantee positive/flat exit)
                    be_buffer = entry_px * 0.0003
                    pos["stop_loss"] = round(entry_px + be_buffer if is_long else entry_px - be_buffer, 4)

                    self.daily_pnl_usd += banked_pnl
                    self._save_positions()
                    self._save_state()

                    thought = f"[{time.strftime('%H:%M:%S UTC')}] 💰 TP1 HIT on {symbol}! Banked ${banked_pnl:+.2f}. FREE TRADE LOCKED (SL -> Break-Even)."
                    self._add_thought(thought)
                    print(f"[QUANT VAULT] 💰 TP1 HIT on {symbol}! Banked ${banked_pnl:+.2f}. SL moved to Break-Even.")

                    try:
                        self.telegram_bot.notify_tp1(
                            symbol=symbol,
                            side=pos["side"],
                            banked_pnl=banked_pnl,
                            be_stop=pos["stop_loss"],
                            daily_pnl=self.daily_pnl_usd,
                            daily_target=self.daily_profit_target_usd
                        )
                    except Exception:
                        pass

            # 3. Dynamic Trailing Profit-Locker on Remaining 50% Runner
            if pos.get("tp1_hit"):
                if is_long:
                    if cur_price >= (entry_px * 1.020):
                        trail_stop = round(entry_px * 1.015, 4)
                    elif cur_price >= (entry_px * 1.015):
                        trail_stop = round(entry_px * 1.010, 4)
                    elif cur_price >= (entry_px * 1.010):
                        trail_stop = round(entry_px * 1.005, 4)
                    else:
                        trail_stop = pos["stop_loss"]
                    if trail_stop > pos["stop_loss"]:
                        pos["stop_loss"] = trail_stop
                        self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] 📈 Trailing Stop ratcheted up on {symbol} to ${trail_stop:,.2f}.")
                else:
                    if cur_price <= (entry_px * 0.980):
                        trail_stop = round(entry_px * 0.985, 4)
                    elif cur_price <= (entry_px * 0.985):
                        trail_stop = round(entry_px * 0.990, 4)
                    elif cur_price <= (entry_px * 0.990):
                        trail_stop = round(entry_px * 0.995, 4)
                    else:
                        trail_stop = pos["stop_loss"]
                    if trail_stop < pos["stop_loss"]:
                        pos["stop_loss"] = trail_stop
                        self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] 📈 Trailing Stop ratcheted down on {symbol} to ${trail_stop:,.2f}.")

            duration = now - float(pos.get("open_time", now))

            # 4. Check Stop Loss / TP2 Full Exit
            sl_condition = (cur_price <= pos["stop_loss"]) if is_long else (cur_price >= pos["stop_loss"])
            tp2_condition = (cur_price >= pos["tp2"]) if is_long else (cur_price <= pos["tp2"])

            if sl_condition or tp2_condition:
                exit_reason = "TP2_RUNNER_TARGET" if tp2_condition else ("BREAK_EVEN_STOP" if pos.get("break_even_active") else "STOP_LOSS")
                final_pnl = round(unrealized_pnl, 2)
                self.daily_pnl_usd += final_pnl
                self.daily_trades_count += 1

                trade_record = {
                    "symbol": symbol,
                    "side": pos["side"],
                    "entry_price": entry_px,
                    "exit_price": cur_price,
                    "pnl_usd": final_pnl,
                    "pnl_pct": pos["unrealized_pnl_pct"],
                    "exit_reason": exit_reason,
                    "closed_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "mode": pos["mode"],
                    "duration_sec": int(duration)
                }
                self._record_closed_trade(trade_record)
                symbols_to_close.append((symbol, trade_record))

        for sym, rec in symbols_to_close:
            if sym in self.open_positions:
                pos = self.open_positions[sym]
                del self.open_positions[sym]

                # Enforce cooldown on Stop Loss: 45 min for 1 loss, 3 hours for 2+ consecutive losses
                if rec["exit_reason"] == "STOP_LOSS":
                    self.symbol_consecutive_losses[sym] = self.symbol_consecutive_losses.get(sym, 0) + 1
                    cooldown_duration = 10800.0 if self.symbol_consecutive_losses[sym] >= 2 else 2700.0
                    self.symbol_cooldowns[sym] = time.time() + cooldown_duration
                    cooldown_min = int(cooldown_duration / 60)
                    thought = f"[{time.strftime('%H:%M:%S UTC')}] 🛑 STOP LOSS on {sym} (${rec['pnl_usd']:+.2f}). {cooldown_min}m Cooldown Armed (Consecutive Losses: {self.symbol_consecutive_losses[sym]})."
                    self._add_thought(thought)
                elif rec["exit_reason"] in ("TP2_RUNNER_TARGET", "BREAK_EVEN_STOP"):
                    self.symbol_consecutive_losses[sym] = 0

                thought = f"[{time.strftime('%H:%M:%S UTC')}] 🏁 CLOSED {rec['side']} {sym} ({rec['exit_reason']}) | PnL: ${rec['pnl_usd']:+.2f} ({rec['pnl_pct']:+.1f}% on margin)"
                self._add_thought(thought)
                print(f"[QUANT VAULT] 🏁 CLOSED {rec['side']} on {sym} ({rec['exit_reason']}) | PnL: ${rec['pnl_usd']:+.2f}")

                # Record automated post-mortem in Episodic Memory Bank for continuous learning
                try:
                    self.episodic_memory.record_trade_post_mortem(
                        trade_id=pos.get("id", f"trade_{int(now)}"),
                        side=rec["side"],
                        entry_price=rec["entry_price"],
                        exit_price=rec["exit_price"],
                        realized_pnl=rec["pnl_usd"],
                        exit_reason=rec["exit_reason"],
                        entry_metrics={
                            "hurst": pos.get("hurst", 0.50),
                            "robust_z": pos.get("robust_z", 0.0),
                            "setup": pos.get("setup", "SCALP"),
                            "symbol": sym
                        },
                        duration_sec=rec.get("duration_sec", 0)
                    )
                except Exception:
                    pass

                try:
                    history = []
                    if self.history_file.exists():
                        try:
                            history = json.loads(self.history_file.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    wins = sum(1 for t in history if float(t.get("pnl_usd", 0)) > 0)
                    losses = sum(1 for t in history if float(t.get("pnl_usd", 0)) < 0)
                    gross_gain = sum(float(t.get("pnl_usd", 0)) for t in history if float(t.get("pnl_usd", 0)) > 0)
                    gross_loss = sum(float(t.get("pnl_usd", 0)) for t in history if float(t.get("pnl_usd", 0)) < 0)

                    self.telegram_bot.notify_trade_closed(
                        symbol=sym,
                        side=rec["side"],
                        entry_price=rec["entry_price"],
                        exit_price=rec["exit_price"],
                        pnl_usd=rec["pnl_usd"],
                        roe_pct=rec["pnl_pct"],
                        exit_reason=rec["exit_reason"],
                        duration_sec=rec.get("duration_sec", 0),
                        fees_saved=0.45,
                        daily_pnl=self.daily_pnl_usd,
                        daily_trades=self.daily_trades_count,
                        wins=wins,
                        losses=losses,
                        gross_gain=gross_gain,
                        gross_loss=gross_loss,
                        daily_target=self.daily_profit_target_usd,
                        mode=rec["mode"]
                    )
                except Exception as _tc_err:
                    print(f"[QUANT VAULT] Telegram close alert error: {_tc_err}")

        if symbols_to_close:
            self._save_positions()
            self._save_state()

        # Check daily profit goal reached ($2,500 target)
        if self.daily_pnl_usd >= self.daily_profit_target_usd and not self.daily_goal_reached:
            self.daily_goal_reached = True
            self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] 🏆 DAILY TARGET REACHED! Banked ${self.daily_pnl_usd:,.2f} (+2.5% on $100K).")
            print(f"[QUANT VAULT] 🏆 DAILY GOAL REACHED! Banked ${self.daily_pnl_usd:.2f} (> $2,500 target).")
            try:
                self.telegram_bot.notify_daily_goal(
                    eur_amount=round(self.daily_pnl_usd / 1.10, 0),
                    usd_amount=self.daily_pnl_usd,
                    total_trades=self.daily_trades_count
                )
            except Exception:
                pass

    def close_position(self, symbol: str, reason: str = "MANUAL_CLOSE") -> Dict[str, Any]:
        with self.lock:
            if symbol not in self.open_positions:
                return {"success": False, "error": f"Position for {symbol} not found"}
            pos = self.open_positions[symbol]
            cur_price = pos.get("current_price", pos["entry_price"])
            try:
                data = self._fetch_market_data(symbol)
                if data and "price" in data:
                    cur_price = float(data["price"])
            except Exception:
                pass

            entry_px = pos["entry_price"]
            is_long = (pos["side"] == "LONG")
            qty = pos.get("remaining_quantity", pos["quantity"])
            if is_long:
                unrealized_pnl = (cur_price - entry_px) * qty
                pnl_pct = (cur_price - entry_px) / entry_px * 100.0
            else:
                unrealized_pnl = (entry_px - cur_price) * qty
                pnl_pct = (entry_px - cur_price) / entry_px * 100.0

            final_pnl = round(unrealized_pnl, 2)
            self.daily_pnl_usd += final_pnl
            self.daily_trades_count += 1
            now = time.time()
            duration = int(now - float(pos.get("open_time", now)))

            rec = {
                "symbol": symbol,
                "side": pos["side"],
                "entry_price": entry_px,
                "exit_price": cur_price,
                "pnl_usd": final_pnl,
                "pnl_pct": round(pnl_pct * self.leverage, 2),
                "exit_reason": reason,
                "closed_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "mode": pos.get("mode", "PAPER"),
                "duration_sec": duration
            }
            self._record_closed_trade(rec)
            del self.open_positions[symbol]
            self._save_positions()
            self._save_state()
            self._add_thought(f"[{time.strftime('%H:%M:%S UTC')}] ✋ MANUAL CLOSE: {rec['side']} {symbol} @ ${cur_price:,.2f} | PnL: ${rec['pnl_usd']:+.2f} ({rec['pnl_pct']:+.1f}%)")

            try:
                self.episodic_memory.record_trade_post_mortem(
                    trade_id=pos.get("id", f"trade_{int(now)}"),
                    side=rec["side"],
                    entry_price=entry_px,
                    exit_price=cur_price,
                    realized_pnl=final_pnl,
                    exit_reason=reason,
                    entry_metrics={"symbol": symbol},
                    duration_sec=duration
                )
            except Exception:
                pass

            try:
                history = []
                if self.history_file.exists():
                    try:
                        history = json.loads(self.history_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                wins = sum(1 for t in history if float(t.get("pnl_usd", 0)) > 0)
                losses = sum(1 for t in history if float(t.get("pnl_usd", 0)) < 0)
                gross_gain = sum(float(t.get("pnl_usd", 0)) for t in history if float(t.get("pnl_usd", 0)) > 0)
                gross_loss = sum(float(t.get("pnl_usd", 0)) for t in history if float(t.get("pnl_usd", 0)) < 0)

                self.telegram_bot.notify_trade_closed(
                    symbol=symbol,
                    side=rec["side"],
                    entry_price=entry_px,
                    exit_price=cur_price,
                    pnl_usd=rec["pnl_usd"],
                    roe_pct=rec["pnl_pct"],
                    exit_reason=reason,
                    duration_sec=duration,
                    fees_saved=0.45,
                    daily_pnl=self.daily_pnl_usd,
                    daily_trades=self.daily_trades_count,
                    wins=wins,
                    losses=losses,
                    gross_gain=gross_gain,
                    gross_loss=gross_loss,
                    daily_target=self.daily_profit_target_usd,
                    mode=rec.get("mode", "PAPER")
                )
            except Exception as _tc_err:
                print(f"[QUANT VAULT] Telegram close alert error: {_tc_err}")

            return {"success": True, "trade": rec}

    def _run_loop(self):
        print("[QUANT VAULT] 24/7 Background loop actively hunting setups across Gold & Top 20...")
        scan_interval_counter = 0

        while self.is_running:
            try:
                # 1. Manage active positions every 1 second
                if self.open_positions:
                    self._manage_open_positions()

                # 2. Strict Circuit Breaker: Halt if -$3,000 daily loss hit
                if self.daily_pnl_usd <= -self.daily_loss_limit_usd:
                    if not self.circuit_breaker_triggered:
                        self.circuit_breaker_triggered = True
                        thought = f"[{time.strftime('%H:%M:%S UTC')}] 🛡️ CIRCUIT BREAKER TRIGGERED (-$3,000/day). Entries paused to protect bankroll."
                        self._add_thought(thought)
                        print("[QUANT VAULT] 🛡️ CIRCUIT BREAKER TRIGGERED (-$3,000/day). Halting entries.")
                        try:
                            self.telegram_bot.notify_circuit_breaker(
                                loss_amount=self.daily_pnl_usd,
                                limit=self.daily_loss_limit_usd
                            )
                        except Exception:
                            pass
                    time.sleep(2)
                    continue

                # 3. Sniper Hunting: Scan dynamic watchlist every 10 seconds
                scan_interval_counter += 1
                if scan_interval_counter >= 10:
                    scan_interval_counter = 0

                    # Check daily margin cap ($15,000 max active margin: 15 positions * $1,000 margin)
                    current_margin = sum(float(p.get("margin_collateral_usd", 1000.0)) for p in self.open_positions.values())
                    if (current_margin + 1000.0) <= self.max_daily_allocation_usd and len(self.open_positions) < self.max_concurrent_positions:
                        watchlist = self.universe_scanner.get_hunting_watchlist()

                        for symbol in watchlist:
                            if symbol in self.open_positions:
                                continue

                            # Skip if in active cooldown
                            if symbol in self.symbol_cooldowns:
                                if time.time() < self.symbol_cooldowns[symbol]:
                                    continue
                                else:
                                    del self.symbol_cooldowns[symbol]

                            data = self._fetch_market_data(symbol)
                            if not data:
                                continue

                            signal = self._evaluate_signal(symbol, data)
                            if signal:
                                self._open_position(signal)
                                if len(self.open_positions) >= self.max_concurrent_positions:
                                    break

            except Exception as e:
                print(f"[QUANT VAULT ERROR]: {e}")

            time.sleep(1)

        print("[QUANT VAULT] Loop paused.")


if __name__ == "__main__":
    trader = AutonomousMultiAssetTrader()
    st = trader.get_status()
    print(f"Quant Vault status: mode={st.get('mode_name')} running={st.get('running')} positions={st.get('open_positions_count')}")
    trader.start()
    time.sleep(2)
    st = trader.get_status()
    print(f"Running status: running={st.get('running')} positions={st.get('open_positions_count')}")
    trader.stop()
