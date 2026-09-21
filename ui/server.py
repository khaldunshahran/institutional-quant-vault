#!/usr/bin/env python3
"""
Upgraded Production Server for Polymarket BTC 5M Trading Cockpit.
Provides real-time telemetry (Binance BTC spot, 5m net impulse, CLOB orderbooks),
wallet balance checking, bot execution manager (one-shot & loop), and logs streaming.
"""

import os
import sys
import json
import time
import signal
import subprocess
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import urllib.parse
import requests
import yaml

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_DIR = PROJECT_ROOT / "ui"
REPORTS_DIR = PROJECT_ROOT / "reports"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

# Load credentials from .env
load_dotenv(PROJECT_ROOT / ".env")

# Initialize ultra-fast StreamManager for real-time WebSocket & connection pooling
try:
    sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.stream_manager import StreamManager
    STREAM_MGR = StreamManager.get_instance()
except Exception:
    STREAM_MGR = None

try:
    from scripts.jev_decision_engine import JevDecisionEngine, MarketStatePayload
    GLOBAL_JEV_ENGINE = JevDecisionEngine()
    print(f"[INIT] JevDecisionEngine initialized! Live ready: {GLOBAL_JEV_ENGINE.is_live_ready()}")
except Exception as e:
    print(f"[WARN] JevDecisionEngine init error: {e}")
    GLOBAL_JEV_ENGINE = None
    MarketStatePayload = None

try:
    from scripts.mtf_technical_engine import MTFTechnicalEngine
    GLOBAL_MTF_ENGINE = MTFTechnicalEngine()
    from scripts.math_quant_engine import MathQuantEngine
    GLOBAL_MATH_ENGINE = MathQuantEngine()
    from scripts.order_flow_engine import OrderFlowEngine
    GLOBAL_ORDER_FLOW_ENGINE = OrderFlowEngine()
    from scripts.macro_telemetry_engine import MacroTelemetryEngine
    GLOBAL_MACRO_ENGINE = MacroTelemetryEngine()
    from scripts.session_clock_engine import SessionClockEngine
    GLOBAL_SESSION_ENGINE = SessionClockEngine()
    from scripts.order_book_engine import OrderBookEngine
    GLOBAL_ORDER_BOOK_ENGINE = OrderBookEngine()
    from scripts.dvol_engine import DvolEngine
    GLOBAL_DVOL_ENGINE = DvolEngine()
    from scripts.economic_calendar_engine import EconomicCalendarEngine
    GLOBAL_NEWS_ENGINE = EconomicCalendarEngine()
    from scripts.smart_execution_engine import SmartExecutionEngine
    GLOBAL_SMART_EXEC = SmartExecutionEngine()
    from scripts.lead_lag_engine import LeadLagEngine
    GLOBAL_LEAD_LAG = LeadLagEngine()
    from scripts.episodic_memory_engine import EpisodicMemoryEngine
    GLOBAL_EPISODIC_MEMORY = EpisodicMemoryEngine()
    from scripts.multi_asset_scanner import MultiAssetScanner
    GLOBAL_MULTI_ASSET_SCANNER = MultiAssetScanner()
    from scripts.liquidation_cascade_engine import LiquidationCascadeEngine
    GLOBAL_LIQUIDATION_ENGINE = LiquidationCascadeEngine()
    from scripts.telegram_alert_bot import TelegramAlertBot
    GLOBAL_TELEGRAM_BOT = TelegramAlertBot()
    from scripts.monte_carlo_engine import MonteCarloEngine
    GLOBAL_MONTE_CARLO = MonteCarloEngine()
    from scripts.jev_directional_engine import JevDirectionalEngine
    GLOBAL_DIRECTIONAL_JEV = JevDirectionalEngine()
    from scripts.binance_universe_scanner import BinanceUniverseScanner
    GLOBAL_BINANCE_SCANNER = BinanceUniverseScanner()
    from scripts.binance_execution_adapter import BinanceExecutionAdapter
    GLOBAL_BINANCE_ADAPTER = BinanceExecutionAdapter()
    from scripts.autonomous_multi_asset_trader import AutonomousMultiAssetTrader
    GLOBAL_AUTONOMOUS_TRADER = AutonomousMultiAssetTrader()
    GLOBAL_AUTONOMOUS_TRADER.telegram_bot = GLOBAL_TELEGRAM_BOT
    GLOBAL_TELEGRAM_BOT.start_polling(
        state_provider=GLOBAL_AUTONOMOUS_TRADER.get_telemetry_state,
        action_handler=GLOBAL_AUTONOMOUS_TRADER.handle_remote_action
    )
    print("[INIT] ALL INSTITUTIONAL QUANT ENGINES (Multi-Asset, Liquidation, Telegram Poller, Monte Carlo) initialized successfully!")
except Exception as e:
    print(f"[WARN] Error initializing quant engines: {e}")
    GLOBAL_MTF_ENGINE = None
    GLOBAL_MATH_ENGINE = None
    GLOBAL_ORDER_FLOW_ENGINE = None
    GLOBAL_DIRECTIONAL_JEV = None


BOT_PROCESS = None
BOT_PROCESS_LOCK = threading.Lock()
CURRENT_BOT_INFO = {
    "running": False,
    "pid": None,
    "profile": "conservative",
    "mode": "dry_run",
    "stake_usd": 5.0,
    "threshold": 0.70,
    "run_mode": "one_shot",  # 'one_shot' or 'continuous_loop'
    "started_at": None,
    "log_file": None,
}



LAST_LONG_SHORT_CALLS = []

LAST_JEV_DECISION = {
    'timestamp': 0,
    'decision': None,
}

CACHED_TELEMETRY = {
    "last_updated": 0,
    "btc_spot": None,
    "btc_open": None,
    "btc_impulse": None,
    "btc_threshold": 70.0,
    "impulse_progress_pct": 0.0,
    "market": {
        "slug": "--",
        "title": "Loading active 5m market...",
        "seconds_left": 0,
        "up_token": "",
        "down_token": "",
        "clob_up": {"bid": None, "ask": None, "spread": None},
        "clob_down": {"bid": None, "ask": None, "spread": None},
        "skew_up_pct": 50.0,
        "skew_down_pct": 50.0,
    },
    "wallet": {
        "address": os.getenv("PM_ADDRESS", "Not Configured"),
        "usdc_balance": "0.00",
        "auth_verified": bool(os.getenv("PM_API_KEY")),
    },
}



GLOBAL_KLINE_CACHE = {}

class DirectionalTradeManager:
    """Real-time Perpetual & Futures Margin Simulator with Auto SL/TP and Institutional Exchange Fees."""
    TAKER_FEE_RATE = 0.0004  # 0.04% taker fee (Binance / Bybit VIP0)
    SLIPPAGE_RATE = 0.0001   # 0.01% execution slippage on market fill

    def __init__(self, trades_file: Path):
        self.trades_file = trades_file
        self.lock = threading.Lock()
        self.balance = 100000.0
        self.active_position = None
        self.closed_trades = []
        self.bot_config = {
            "auto_trade": False,
            "min_confidence": 70.0,
            "risk_pct": 2.0,
            "leverage": 20,
            "take_half_at_tp1": True,
            "move_sl_to_be": True,
            "include_fees": True,
        }
        self.last_auto_exit_ts = 0.0
        self.cumulative_fees_saved = 0.0
        self.load_trades()

    def load_trades(self):
        try:
            if self.trades_file.exists():
                import json
                with open(self.trades_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.balance = float(data.get("balance", 1000.0))
                        self.closed_trades = data.get("trades", [])
                        if "bot_config" in data and isinstance(data["bot_config"], dict):
                            self.bot_config.update(data["bot_config"])
                    elif isinstance(data, list):
                        self.closed_trades = data
        except Exception as e:
            print(f"Error loading directional trades: {e}")

    def save_trades(self):
        try:
            import json
            self.trades_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.trades_file, "w", encoding="utf-8") as f:
                json.dump({
                    "balance": round(self.balance, 2),
                    "bot_config": self.bot_config,
                    "trades": self.closed_trades[:100]
                }, f, indent=2)
        except Exception as e:
            print(f"Error saving directional trades: {e}")

    def update_config(self, new_config: dict):
        with self.lock:
            if "auto_trade" in new_config:
                self.bot_config["auto_trade"] = bool(new_config["auto_trade"])
            if "min_confidence" in new_config:
                self.bot_config["min_confidence"] = float(new_config["min_confidence"])
            if "risk_pct" in new_config:
                self.bot_config["risk_pct"] = float(new_config["risk_pct"])
            if "leverage" in new_config:
                self.bot_config["leverage"] = int(new_config["leverage"])
            if "take_half_at_tp1" in new_config:
                self.bot_config["take_half_at_tp1"] = bool(new_config["take_half_at_tp1"])
            if "move_sl_to_be" in new_config:
                self.bot_config["move_sl_to_be"] = bool(new_config["move_sl_to_be"])
            self.save_trades()

    def reset_account(self):
        with self.lock:
            self.balance = 1000.0
            self.active_position = None
            self.closed_trades = []
            self.save_trades()

    def open_position(self, side: str, entry_price: float, leverage: int = 20, risk_pct: float = 2.0,
                      balance: float = None, custom_sl: float = None, custom_tp1: float = None, custom_tp2: float = None,
                      rationale: str = ""):
        with self.lock:
            if self.active_position is not None:
                return {"success": False, "error": "Position already active. Close it first before opening a new trade."}

            if balance is not None and balance > 0:
                self.balance = balance

            leverage = max(1, min(50, int(leverage)))
            risk_pct = max(0.5, min(10.0, float(risk_pct)))
            dollar_risk = self.balance * (risk_pct / 100.0)

            # Apply realistic fill slippage (0.01%)
            if side == "LONG":
                fill_price = round(entry_price * (1.0 + self.SLIPPAGE_RATE), 2)
            else:
                fill_price = round(entry_price * (1.0 - self.SLIPPAGE_RATE), 2)

            default_dist = fill_price * 0.005
            if side == "LONG":
                stop_loss = custom_sl if (custom_sl and custom_sl < fill_price) else round(fill_price - default_dist, 2)
                tp1 = custom_tp1 if (custom_tp1 and custom_tp1 > fill_price) else round(fill_price + default_dist * 1.6, 2)
                tp2 = custom_tp2 if (custom_tp2 and custom_tp2 > fill_price) else round(fill_price + default_dist * 3.2, 2)
                sl_dist = abs(fill_price - stop_loss)
            else:
                stop_loss = custom_sl if (custom_sl and custom_sl > fill_price) else round(fill_price + default_dist, 2)
                tp1 = custom_tp1 if (custom_tp1 and custom_tp1 < fill_price) else round(fill_price - default_dist * 1.6, 2)
                tp2 = custom_tp2 if (custom_tp2 and custom_tp2 < fill_price) else round(fill_price - default_dist * 3.2, 2)
                sl_dist = abs(stop_loss - fill_price)

            if sl_dist <= 0:
                sl_dist = fill_price * 0.003

            risk_fraction = sl_dist / fill_price
            ideal_size_usd = dollar_risk / risk_fraction
            max_size_usd = self.balance * leverage
            size_usd = round(min(ideal_size_usd, max_size_usd), 2)
            margin = round(size_usd / leverage, 2)
            size_btc = round(size_usd / fill_price, 4)

            # Smart Maker Fee on Entry (0.015% on notional position)
            open_fee = round(size_usd * self.MAKER_FEE_RATE, 2)
            taker_fee_would_be = round(size_usd * self.TAKER_FEE_RATE, 2)
            fee_saved_entry = round(taker_fee_would_be - open_fee, 2)
            self.cumulative_fees_saved = round(self.cumulative_fees_saved + fee_saved_entry, 2)
            est_close_fee = open_fee
            total_est_fees = round(open_fee + est_close_fee, 2)

            if side == "LONG":
                liq_price = round(fill_price * (1.0 - (1.0 / leverage) * 0.9), 2)
            else:
                liq_price = round(fill_price * (1.0 + (1.0 / leverage) * 0.9), 2)

            import time
            pos_id = f"pos_{int(time.time()*1000)}"
            self.active_position = {
                "id": pos_id,
                "symbol": "BTCUSDT",
                "side": side,
                "entry_price": fill_price,
                "current_price": fill_price,
                "size_usd": size_usd,
                "size_btc": size_btc,
                "margin": margin,
                "leverage": leverage,
                "stop_loss": round(stop_loss, 2),
                "initial_stop_loss": round(stop_loss, 2),
                "sl_dist_orig": sl_dist,
                "tp1": round(tp1, 2),
                "tp2": round(tp2, 2),
                "tp1_hit": False,
                "breakeven_protected": False,
                "trailing_active": False,
                "partial_profit_banked": 0.0,
                "liquidation_price": liq_price,
                "open_fee": open_fee,
                "est_close_fee": est_close_fee,
                "total_fees": total_est_fees,
                "gross_unrealized_pnl": 0.0,
                "unrealized_pnl": -total_est_fees,  # Starts negative due to round-trip fees!
                "roe_pct": round((-total_est_fees / margin) * 100.0, 2) if margin > 0 else 0.0,
                "opened_at": time.strftime("%H:%M:%S UTC", time.gmtime()),
                "opened_ts": time.time(),
                "rationale": rationale or f"Manual {side} Execution",
                "status": "OPEN"
            }
            return {"success": True, "position": self.active_position}

    def close_position(self, exit_price: float, reason: str = "MANUAL_EXIT"):
        with self.lock:
            if not self.active_position:
                return {"success": False, "error": "No open position to close."}

            import time
            pos = self.active_position
            side = pos["side"]
            size_btc = pos["size_btc"]
            size_usd = pos["size_usd"]
            entry_price = pos["entry_price"]
            margin = pos["margin"]
            open_fee = pos.get("open_fee", round(size_usd * self.TAKER_FEE_RATE, 2))

            # Apply realistic exit slippage (0.01%)
            if side == "LONG":
                fill_exit = round(exit_price * (1.0 - self.SLIPPAGE_RATE), 2)
                gross_pnl = round(size_btc * (fill_exit - entry_price), 2)
            else:
                fill_exit = round(exit_price * (1.0 + self.SLIPPAGE_RATE), 2)
                gross_pnl = round(size_btc * (entry_price - fill_exit), 2)

            # Smart Maker Fee on Exit if TP hit, else Taker fee on emergency SL
            is_maker_exit = ("TAKE_PROFIT" in reason)
            fee_rate = self.MAKER_FEE_RATE if is_maker_exit else self.TAKER_FEE_RATE
            close_fee = round(size_usd * fee_rate, 2)
            if is_maker_exit:
                fee_saved_exit = round((size_usd * self.TAKER_FEE_RATE) - close_fee, 2)
                self.cumulative_fees_saved = round(self.cumulative_fees_saved + fee_saved_exit, 2)
            total_fees = round(open_fee + close_fee, 2)
            net_pnl = round(gross_pnl - total_fees, 2)

            # Record automated post-mortem in Episodic Memory Bank
            try:
                if 'GLOBAL_EPISODIC_MEMORY' in globals() and GLOBAL_EPISODIC_MEMORY:
                    GLOBAL_EPISODIC_MEMORY.record_trade_post_mortem(
                        trade_id=pos["id"],
                        side=side,
                        entry_price=entry_price,
                        exit_price=fill_exit,
                        realized_pnl=net_pnl,
                        exit_reason=reason,
                        entry_metrics=pos.get("entry_metrics", {}),
                        duration_sec=duration
                    )
            except Exception as _em_err:
                pass

            net_roe = round((net_pnl / margin) * 100.0, 2) if margin > 0 else 0.0
            duration = int(time.time() - pos.get("opened_ts", time.time()))

            trade_record = {
                "id": pos["id"],
                "symbol": pos["symbol"],
                "side": side,
                "entry_price": entry_price,
                "exit_price": fill_exit,
                "size_usd": size_usd,
                "size_btc": size_btc,
                "margin": margin,
                "leverage": pos["leverage"],
                "gross_pnl": gross_pnl,
                "fees_paid": total_fees,
                "realized_pnl": net_pnl,  # Net Realized PnL after all fees!
                "roe_pct": net_roe,
                "opened_at": pos["opened_at"],
                "closed_at": time.strftime("%H:%M:%S UTC", time.gmtime()),
                "duration_sec": duration,
                "exit_reason": reason,
                "rationale": pos.get("rationale", "")
            }

            self.balance = round(self.balance + net_pnl, 2)
            self.closed_trades.insert(0, trade_record)
            self.active_position = None
            self.last_auto_exit_ts = time.time()
            self.save_trades()

            # Dispatch real-time Telegram trade closure alert
            try:
                if 'GLOBAL_TELEGRAM_BOT' in globals() and GLOBAL_TELEGRAM_BOT:
                    daily_trades = [t for t in self.closed_trades if (time.time() - float(t.get("opened_ts", 0))) <= 86400.0]
                    daily_pnl = sum(float(t.get("realized_pnl", 0)) for t in daily_trades)
                    wins = sum(1 for t in daily_trades if float(t.get("realized_pnl", 0)) > 0)
                    losses = sum(1 for t in daily_trades if float(t.get("realized_pnl", 0)) <= 0)
                    GLOBAL_TELEGRAM_BOT.notify_trade_closed(
                        symbol=pos.get("symbol", "BTCUSDT"),
                        side=side,
                        entry_price=entry_price,
                        exit_price=fill_exit,
                        pnl_usd=net_pnl,
                        roe_pct=net_roe,
                        exit_reason=reason,
                        duration_sec=duration,
                        fees_saved=round(self.cumulative_fees_saved, 2),
                        daily_pnl=daily_pnl,
                        daily_trades=len(daily_trades),
                        wins=wins,
                        losses=losses,
                        daily_target=110.0,
                        mode="PAPER"
                    )
            except Exception as _tg_err:
                pass

            return {"success": True, "closed_trade": trade_record, "new_balance": self.balance}

    def on_price_tick(self, spot_price: float, latest_signal: dict = None):
        with self.lock:
            import time
            now = time.time()
            if self.active_position:
                pos = self.active_position
                pos["current_price"] = round(spot_price, 2)
                side = pos["side"]
                entry_price = pos["entry_price"]
                size_btc = pos["size_btc"]
                size_usd = pos["size_usd"]
                margin = pos["margin"]
                stop_loss = pos["stop_loss"]
                tp1 = pos["tp1"]
                tp2 = pos["tp2"]

                if side == "LONG":
                    gross_pnl = round(size_btc * (spot_price - entry_price), 2)
                else:
                    gross_pnl = round(size_btc * (entry_price - spot_price), 2)

                open_fee = pos.get("open_fee", round(size_usd * self.TAKER_FEE_RATE, 2))
                est_close_fee = round(size_usd * self.TAKER_FEE_RATE, 2)
                total_fees = round(open_fee + est_close_fee, 2)
                net_pnl = round(gross_pnl - total_fees, 2)

                pos["gross_unrealized_pnl"] = gross_pnl
                pos["unrealized_pnl"] = net_pnl
                pos["roe_pct"] = round((net_pnl / margin) * 100.0, 2) if margin > 0 else 0.0
                pos["total_fees"] = total_fees

                # Invalidation Stop Loss Trigger
                hit_sl = (side == "LONG" and spot_price <= stop_loss) or (side == "SHORT" and spot_price >= stop_loss)
                if hit_sl:
                    self.lock.release()
                    self.close_position(spot_price, reason="STOP_LOSS")
                    self.lock.acquire()
                    return

                # Take Profit 2 (Full exit)
                hit_tp2 = (side == "LONG" and spot_price >= tp2) or (side == "SHORT" and spot_price <= tp2)
                if hit_tp2:
                    self.lock.release()
                    self.close_position(spot_price, reason="TAKE_PROFIT_2")
                    self.lock.acquire()
                    return

                # 1. Take Profit 1: Scale out 50% & Exact Fee-Protected Breakeven
                hit_tp1 = (side == "LONG" and spot_price >= tp1) or (side == "SHORT" and spot_price <= tp1)
                if hit_tp1 and not pos.get("tp1_hit"):
                    pos["tp1_hit"] = True
                    # Exact fee-protected breakeven (entry + roundtrip fees + slippage buffer)
                    fee_buffer = round(entry_price * (self.TAKER_FEE_RATE * 2.0 + self.SLIPPAGE_RATE * 2.0), 2)
                    be_price = round(entry_price + (fee_buffer if side == "LONG" else -fee_buffer), 2)
                    pos["stop_loss"] = be_price
                    pos["breakeven_protected"] = True
                    
                    if self.bot_config.get("take_half_at_tp1", True):
                        locked_pnl = round((gross_pnl * 0.5) - (total_fees * 0.5), 2)
                        self.balance = round(self.balance + locked_pnl, 2)
                        pos["size_usd"] = round(pos["size_usd"] * 0.5, 2)
                        pos["size_btc"] = round(pos["size_btc"] * 0.5, 4)
                        pos["margin"] = round(pos["margin"] * 0.5, 2)
                        pos["open_fee"] = round(pos["open_fee"] * 0.5, 2)
                        pos["partial_profit_banked"] = locked_pnl
                    self.save_trades()
                    try:
                        if 'GLOBAL_TELEGRAM_BOT' in globals() and GLOBAL_TELEGRAM_BOT:
                            GLOBAL_TELEGRAM_BOT.notify_tp1(
                                symbol=pos["symbol"],
                                side=side,
                                banked_pnl=pos.get("partial_profit_banked", 0.0),
                                be_stop=pos["stop_loss"]
                            )
                    except Exception:
                        pass

                # 2. Dynamic Trailing Stop (When price advances past +1.8 ATR toward TP2, lock in +0.8 ATR)
                orig_dist = float(pos.get("sl_dist_orig", 400.0))
                if pos.get("tp1_hit"):
                    if side == "LONG" and spot_price >= (entry_price + orig_dist * 1.8):
                        trail_sl = round(entry_price + orig_dist * 0.8, 2)
                        if trail_sl > pos["stop_loss"]:
                            pos["stop_loss"] = trail_sl
                            pos["trailing_active"] = True
                            self.save_trades()
                    elif side == "SHORT" and spot_price <= (entry_price - orig_dist * 1.8):
                        trail_sl = round(entry_price - orig_dist * 0.8, 2)
                        if trail_sl < pos["stop_loss"]:
                            pos["stop_loss"] = trail_sl
                            pos["trailing_active"] = True
                            self.save_trades()



            elif self.bot_config.get("auto_trade", False) and latest_signal:
                bias = latest_signal.get("signal_type")
                # QUANT SUPERPOWER GATES: Block if Random Walk or EV Hurdle failed!
                is_rw = latest_signal.get("is_random_walk", False)
                ev_ok = latest_signal.get("ev_hurdle_passed", True)
                is_news = latest_signal.get("is_news_blackout", False)

                # Check 24-Hour Max Loss Circuit Breaker (-$40 limit)
                daily_trades = [t for t in self.closed_trades if (now - float(t.get("opened_ts", 0))) <= 86400.0]
                daily_pnl = sum(float(t.get("realized_pnl", 0)) for t in daily_trades)
                if daily_pnl <= -40.0:
                    # Circuit breaker active: Freeze all trading for the day to preserve bankroll
                    return

                if bias in ("LONG", "SHORT") and not is_rw and ev_ok and not is_news:
                    conf = float(latest_signal.get("confidence", 0.0))
                    if conf <= 1.0:
                        conf = conf * 100.0  # normalize decimal to percentage
                    min_conf = float(self.bot_config.get("min_confidence", 70.0))
                    
                    # 60-second cooldown after exits to prevent thrashing
                    if conf >= min_conf and (now - self.last_auto_exit_ts) > 60.0:
                        # JEV AUTONOMOUS AUTHORITY: Jev chooses dynamic leverage, stops, and risk %
                        lev = int(latest_signal.get("dynamic_leverage") or self.bot_config.get("leverage", 5))
                        risk = float(latest_signal.get("dynamic_risk_pct") or self.bot_config.get("risk_pct", 1.5))
                        sl = latest_signal.get("stop_loss")
                        tp1 = latest_signal.get("take_profit_1")
                        tp2 = latest_signal.get("take_profit_2")
                        lbl = latest_signal.get("signal_label", bias)
                        stat = latest_signal.get("status_description", "")
                        ev_val = latest_signal.get("expected_value_usd", 0.0)
                        
                        self.lock.release()
                        entry_metrics = {
                            "hurst": latest_signal.get("hurst_exponent", 0.5),
                            "obi": latest_signal.get("order_book_obi", 0.0),
                            "perp_cvd_15m": latest_signal.get("perp_cvd_15m", 0.0),
                            "session_name": latest_signal.get("session_name", "UNKNOWN"),
                            "robust_z": latest_signal.get("robust_z", 0.0),
                            "strategy_mode": latest_signal.get("strategy_mode", "TREND_EXPANSION")
                        }
                        res = self.open_position(
                            side=bias,
                            entry_price=spot_price,
                            leverage=lev,
                            risk_pct=risk,
                            custom_sl=sl,
                            custom_tp1=tp1,
                            custom_tp2=tp2,
                            rationale=f"Auto Jev Signal: {lbl} ({conf:.0f}% conf, {lev}x, EV +${ev_val:.2f}) | {stat[:80]}"
                        )
                        if res.get("success") and self.active_position:
                            self.active_position["entry_metrics"] = entry_metrics
                        self.lock.acquire()

    def get_state(self):
        with self.lock:
            import time
            now_ts = time.time()
            total_trades = len(self.closed_trades)
            wins = sum(1 for t in self.closed_trades if float(t.get("realized_pnl", 0)) > 0)
            losses = sum(1 for t in self.closed_trades if float(t.get("realized_pnl", 0)) <= 0)
            win_rate = round((wins / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
            net_pnl = round(sum(float(t.get("realized_pnl", 0)) for t in self.closed_trades), 2)
            gross_win = sum(float(t.get("realized_pnl", 0)) for t in self.closed_trades if float(t.get("realized_pnl", 0)) > 0)
            gross_loss = abs(sum(float(t.get("realized_pnl", 0)) for t in self.closed_trades if float(t.get("realized_pnl", 0)) < 0))
            profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
            total_fees_paid = round(sum(float(t.get("fees_paid", 0)) for t in self.closed_trades), 2)

            # Daily 24h Performance & Target Calculations
            daily_trades = [t for t in self.closed_trades if (now_ts - float(t.get("opened_ts", 0))) <= 86400.0]
            daily_pnl = round(sum(float(t.get("realized_pnl", 0)) for t in daily_trades), 2)
            daily_target_usd = 110.0  # €100 target (~$110)
            daily_progress_pct = round(max(0.0, min(100.0, (daily_pnl / daily_target_usd) * 100.0)), 1)
            circuit_breaker_tripped = (daily_pnl <= -40.0)  # Max daily loss cap: -$40 (4%)
            daily_goal_reached = (daily_pnl >= daily_target_usd)

            return {
                "balance": round(self.balance, 2),
                "active_position": self.active_position,
                "bot_config": self.bot_config,
                "stats": {
                    "total_trades": total_trades,
                    "wins": wins,
                    "losses": losses,
                    "win_rate_pct": win_rate,
                    "net_pnl": net_pnl,
                    "profit_factor": profit_factor,
                    "total_fees_paid": total_fees_paid,
                    "cumulative_fees_saved": self.cumulative_fees_saved,
                    "execution_mode": "SMART_MAKER_POST_ONLY"
                },
                "daily_goal": {
                    "target_eur": 100.0,
                    "target_usd": daily_target_usd,
                    "current_pnl": daily_pnl,
                    "progress_pct": daily_progress_pct,
                    "circuit_breaker_tripped": circuit_breaker_tripped,
                    "max_daily_loss_usd": 40.0,
                    "goal_reached": daily_goal_reached,
                    "trades_today": len(daily_trades)
                },
                "closed_trades": self.closed_trades[:35]
            }

DIRECTIONAL_MANAGER = DirectionalTradeManager(RUNTIME_DIR / "directional_trades.json")



import collections

OI_HISTORY = collections.deque(maxlen=120)
INSTITUTIONAL_CACHE = {
    "open_interest_btc": 108000.0,
    "open_interest_usd": 8750000000.0,
    "oi_usd_formatted": "$8.75B",
    "oi_5m_delta_usd": 0.0,
    "oi_5m_delta_formatted": "+$0.0M",
    "coinbase_spot": 81050.0,
    "coinbase_premium": 0.0,
    "funding_rate": 0.0001,
    "funding_rate_pct": 0.0100,
    "last_updated": 0
}

def institutional_telemetry_worker():
    """Background worker continuously ingesting Binance Futures OI, Funding Rates, and Coinbase Spot."""
    global OI_HISTORY, INSTITUTIONAL_CACHE, CACHED_TELEMETRY
    import urllib.request, json
    while True:
        try:
            now = time.time()
            # Current spot reference
            btc_spot = CACHED_TELEMETRY.get("binance", {}).get("spot") or 81050.0

            # 1. Binance Futures OI
            oi_btc = INSTITUTIONAL_CACHE.get("open_interest_btc", 108000.0)
            try:
                req = urllib.request.urlopen("https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT", timeout=2.5)
                d = json.loads(req.read().decode())
                if "openInterest" in d:
                    oi_btc = float(d["openInterest"])
            except Exception:
                pass

            # 2. Funding Rate
            funding_rate = INSTITUTIONAL_CACHE.get("funding_rate", 0.0001)
            try:
                req = urllib.request.urlopen("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT", timeout=2.5)
                d = json.loads(req.read().decode())
                if "lastFundingRate" in d:
                    funding_rate = float(d["lastFundingRate"])
            except Exception:
                pass

            # 3. Coinbase Spot
            cb_spot = btc_spot
            try:
                req = urllib.request.Request("https://api.coinbase.com/v2/prices/BTC-USD/spot", headers={"User-Agent": "Mozilla/5.0"})
                d = json.loads(urllib.request.urlopen(req, timeout=2.5).read().decode())
                if "data" in d and "amount" in d["data"]:
                    cb_spot = float(d["data"]["amount"])
            except Exception:
                pass

            oi_usd = oi_btc * btc_spot
            OI_HISTORY.append((now, oi_usd))

            # Compute 5M delta
            cutoff = now - 300.0
            past_oi = oi_usd
            for t_stamp, past_val in OI_HISTORY:
                if t_stamp <= cutoff or len(OI_HISTORY) > 2:
                    past_oi = past_val
                    break
            oi_delta = oi_usd - past_oi
            cb_premium = cb_spot - btc_spot

            INSTITUTIONAL_CACHE = {
                "open_interest_btc": round(oi_btc, 2),
                "open_interest_usd": round(oi_usd, 2),
                "oi_usd_formatted": f"${oi_usd/1e9:.2f}B" if oi_usd >= 1e9 else f"${oi_usd/1e6:.1f}M",
                "oi_5m_delta_usd": round(oi_delta, 2),
                "oi_5m_delta_formatted": f"{'+' if oi_delta >= 0 else ''}${oi_delta/1e6:.1f}M",
                "coinbase_spot": round(cb_spot, 2),
                "coinbase_premium": round(cb_premium, 2),
                "funding_rate": funding_rate,
                "funding_rate_pct": round(funding_rate * 100, 4),
                "last_updated": now
            }
            CACHED_TELEMETRY["institutional"] = INSTITUTIONAL_CACHE
        except Exception:
            pass
        time.sleep(3.0)

def telemetry_background_worker():
    """Background worker thread polling Binance BTC price, Polymarket CLOB book, and wallet balance."""
    global CACHED_TELEMETRY
    while True:
        try:
            update_telemetry()
        except Exception as e:
            # print(f"Telemetry update error: {e}")
            pass
        time.sleep(1.5)


def update_telemetry():
    global CACHED_TELEMETRY
    now = int(time.time())
    cur_5m = now - (now % 300)
    sec_left = max(0, int(cur_5m + 300 - time.time()))

    # 1. Fetch Binance Spot & 5M Open via real-time stream
    btc_spot = None
    btc_open = None
    impulse = None
    if STREAM_MGR is not None:
        btc_spot, btc_open, impulse = STREAM_MGR.get_btc_telemetry(cur_5m)

    if btc_spot is None or btc_open is None:
        try:
            r_ticker = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"}, timeout=2)
            if r_ticker.status_code == 200:
                btc_spot = float(r_ticker.json().get("price", 0))

            r_kline = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "5m", "startTime": cur_5m * 1000, "limit": 1},
                timeout=2,
            )
            if r_kline.status_code == 200 and r_kline.json():
                btc_open = float(r_kline.json()[0][1])
        except Exception:
            pass

    if btc_spot is not None:
        try:
            current_c = compute_current_long_short_call()
            DIRECTIONAL_MANAGER.on_price_tick(btc_spot, current_c)
        except Exception:
            pass

    if impulse is None and (btc_spot is not None and btc_open is not None):
        impulse = btc_spot - btc_open
    progress_pct = 0.0
    if impulse is not None:
        progress_pct = min(100.0, max(0.0, (abs(impulse) / 70.0) * 100.0))

    # 2. Fetch Polymarket 5M Active Event
    slug = f"btc-updown-5m-{cur_5m}"
    mkt_info = {
        "slug": slug,
        "title": "BTC 5m Interval",
        "seconds_left": sec_left,
        "close_at_ts": cur_5m + 300,
        "up_token": "",
        "down_token": "",
        "clob_up": {"bid": None, "ask": None, "spread": None},
        "clob_down": {"bid": None, "ask": None, "spread": None},
        "skew_up_pct": 50.0,
        "skew_down_pct": 50.0,
    }

    last_mkt_fetch = CACHED_TELEMETRY.get("_last_pm_fetch", 0)
    cached_mkt = CACHED_TELEMETRY.get("market")
    if cached_mkt and (now - last_mkt_fetch) < 10.0 and cached_mkt.get("slug") == slug:
        mkt_info = dict(cached_mkt)
        mkt_info["seconds_left"] = sec_left
    else:
        try:
            r_ev = requests.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=1.5)
            if r_ev.status_code == 200 and r_ev.json():
                ev = r_ev.json()[0]
                mkt_info["title"] = ev.get("title", slug)
                mkts = ev.get("markets") or []
                if mkts:
                    m = mkts[0]
                    tokens_raw = m.get("clobTokenIds")
                    tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw or []
                    if len(tokens) >= 2:
                        mkt_info["up_token"] = tokens[0]
                        mkt_info["down_token"] = tokens[1]

                        # Query CLOB orderbooks in parallel using connection pool
                        try:
                            if STREAM_MGR is not None:
                                up_bid, up_ask, dn_bid, dn_ask, _ = STREAM_MGR.get_full_orderbooks(tokens[0], tokens[1])
                            else:
                                from py_clob_client.client import ClobClient
                                pub = ClobClient("https://clob.polymarket.com", chain_id=137)
                                b_up = pub.get_order_book(tokens[0])
                                b_dn = pub.get_order_book(tokens[1])
                                up_ask = float(b_up.asks[0].price) if b_up.asks else None
                                up_bid = float(b_up.bids[0].price) if b_up.bids else None
                                dn_ask = float(b_dn.asks[0].price) if b_dn.asks else None
                                dn_bid = float(b_dn.bids[0].price) if b_dn.bids else None

                            mkt_info["clob_up"]["ask"] = up_ask
                            mkt_info["clob_up"]["bid"] = up_bid
                            mkt_info["clob_up"]["spread"] = round(up_ask - up_bid, 3) if (up_ask and up_bid) else None

                            mkt_info["clob_down"]["ask"] = dn_ask
                            mkt_info["clob_down"]["bid"] = dn_bid
                            mkt_info["clob_down"]["spread"] = round(dn_ask - dn_bid, 3) if (dn_ask and dn_bid) else None

                            # Implied Skew
                            if up_ask and dn_ask:
                                tot = up_ask + dn_ask
                                mkt_info["skew_up_pct"] = round((up_ask / tot) * 100, 1)
                                mkt_info["skew_down_pct"] = round((dn_ask / tot) * 100, 1)
                        except Exception:
                            pass
        except Exception:
            pass
        CACHED_TELEMETRY["_last_pm_fetch"] = now

    # 3. Fetch Wallet USDC Balance periodically (every 10s)
    wallet_info = CACHED_TELEMETRY.get("wallet", {})
    if now - CACHED_TELEMETRY.get("last_wallet_check", 0) > 10:
        try:
            key = os.getenv("PM_PRIVATE_KEY")
            funder = os.getenv("PM_ADDRESS")
            sig = int(os.getenv("PM_SIGNATURE_TYPE", "2"))
            v1 = os.getenv("PM_API_KEY")
            v2 = os.getenv("PM_API_SECRET")
            v3 = os.getenv("PM_API_PASSPHRASE")

            if key and v1 and v2 and v3:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

                c = ClobClient("https://clob.polymarket.com", chain_id=137, key=key, signature_type=sig, funder=funder)
                c.set_api_creds(ApiCreds(api_key=v1, api_secret=v2, api_passphrase=v3))
                bal_obj = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                raw_bal = float(bal_obj.get("balance", "0"))
                # USDC has 6 decimals on Polygon
                clean_bal = raw_bal / 1e6 if raw_bal > 1000 else raw_bal
                wallet_info["usdc_balance"] = f"{clean_bal:.2f}"
                wallet_info["auth_verified"] = True
        except Exception:
            pass
        CACHED_TELEMETRY["last_wallet_check"] = now

    CACHED_TELEMETRY.update({
        "last_updated": now,
        "server_time_ms": int(time.time() * 1000),
        "btc_spot": btc_spot,
        "btc_open": btc_open,
        "btc_impulse": round(impulse, 2) if impulse is not None else None,
        "btc_threshold": 70.0,
        "impulse_progress_pct": round(progress_pct, 1),
        "market": mkt_info,
        "wallet": wallet_info,
    })


def compute_current_long_short_call():
    global GLOBAL_MTF_ENGINE, GLOBAL_DIRECTIONAL_JEV, LAST_LONG_SHORT_CALLS, STREAM_MGR, CACHED_TELEMETRY, INSTITUTIONAL_CACHE
    now = time.time()
    cur_5m = int(now // 300) * 300
    btc_spot = 0.0
    btc_open = 0.0
    impulse = 0.0

    if STREAM_MGR:
        s_spot, s_open, s_imp = STREAM_MGR.get_btc_telemetry(cur_5m)
        if s_spot: btc_spot = s_spot
        if s_open: btc_open = s_open
        if s_imp is not None: impulse = s_imp

    if not btc_spot:
        bin_data = CACHED_TELEMETRY.get('binance', {})
        btc_spot = bin_data.get('spot', 81250.0)
        btc_open = bin_data.get('open', 81250.0)
        impulse = bin_data.get('impulse', 0.0)

    vel = STREAM_MGR.get_velocity_metrics() if STREAM_MGR else {
        'velocity_15s': 0.0,
        'velocity_60s': 0.0,
        'acceleration': 0.0,
        'latency_ms': 0.5,
    }

    # 1. Fetch Multi-Timeframe Technical Indicators
    mtf_data = GLOBAL_MTF_ENGINE.compute_all_indicators(btc_spot) if GLOBAL_MTF_ENGINE else {
        'macro_alignment': 'CHOPPY_RANGE', 'summary_text': 'MTF loading...', 'timeframes': {}
    }
    inst = INSTITUTIONAL_CACHE or {}

    # 2. Fetch Math Quant, Order Flow, Macro, Session, L2 Depth, DVOL, News, Lead-Lag & Memory
    math_data = GLOBAL_MATH_ENGINE.get_quant_metrics() if GLOBAL_MATH_ENGINE else {}
    of_data = GLOBAL_ORDER_FLOW_ENGINE.get_order_flow_metrics() if GLOBAL_ORDER_FLOW_ENGINE else {}
    macro_data = GLOBAL_MACRO_ENGINE.get_macro_state() if GLOBAL_MACRO_ENGINE else {}
    session_data = GLOBAL_SESSION_ENGINE.get_session_and_liquidity_state(btc_spot) if GLOBAL_SESSION_ENGINE else {}
    order_book_data = GLOBAL_ORDER_BOOK_ENGINE.get_order_book_state(btc_spot) if GLOBAL_ORDER_BOOK_ENGINE else {}
    dvol_data = GLOBAL_DVOL_ENGINE.get_dvol_state() if GLOBAL_DVOL_ENGINE else {}
    news_data = GLOBAL_NEWS_ENGINE.get_news_shield_state() if GLOBAL_NEWS_ENGINE else {}
    lead_lag_data = GLOBAL_LEAD_LAG.get_lead_lag_state(btc_spot) if GLOBAL_LEAD_LAG else {}
    liq_data = GLOBAL_LIQUIDATION_ENGINE.get_liquidation_state(btc_spot) if GLOBAL_LIQUIDATION_ENGINE else {}
    scanner_data = GLOBAL_MULTI_ASSET_SCANNER.scan_all_assets() if GLOBAL_MULTI_ASSET_SCANNER else {}
    memory_data = GLOBAL_EPISODIC_MEMORY.retrieve_relevant_lessons(
        "LONG", session_data.get("session_name", "UNKNOWN"), math_data.get("hurst", 0.5)
    ) if GLOBAL_EPISODIC_MEMORY else {}

    # 3. Dedicated Directional Decision Engine
    if GLOBAL_DIRECTIONAL_JEV:
        dec = GLOBAL_DIRECTIONAL_JEV.evaluate_directional_trade(
            mtf_data, inst, btc_spot, math_data, of_data, macro_data, session_data,
            order_book_data, dvol_data, news_data, lead_lag_data, memory_data
        )
    else:
        from scripts.jev_directional_engine import DirectionalDecisionResult
        dec = DirectionalDecisionResult(
            signal_type="NEUTRAL", signal_label="ENGINE LOADING", confidence=0.5, conviction_score=2.0,
            counter_trend_trap_risk=0.5, macro_regime=mtf_data.get('macro_alignment', 'CHOPPY_RANGE'),
            dynamic_leverage=5, dynamic_stop_loss=btc_spot-500.0, dynamic_tp1=btc_spot+750.0, dynamic_tp2=btc_spot+1500.0,
            dynamic_risk_pct=1.5, sl_distance_usd=500.0, tp1_distance_usd=750.0, risk_reward_ratio="1 : 1.5",
            status_description="Waiting for Directional Jev engine...", engine_mode="loading", latency_ms=0.0,
            timestamp_utc=time.strftime('%H:%M:%S UTC', time.gmtime()), mtf_summary=""
        )

    current_call = {
        'signal_type': dec.signal_type,
        'signal_label': dec.signal_label,
        'btc_spot': btc_spot,
        'btc_open': btc_open,
        'impulse': impulse,
        'velocity_15s': vel.get('velocity_15s', 0.0),
        'acceleration': vel.get('acceleration', 0.0),
        'regime': dec.macro_regime,
        'confidence': dec.confidence,
        'conviction_score': dec.conviction_score,
        'reversal_risk': dec.counter_trend_trap_risk,
        
        # Jev Autonomous Authority Parameters
        'dynamic_leverage': dec.dynamic_leverage,
        'dynamic_risk_pct': dec.dynamic_risk_pct,
        'entry_zone': [round(btc_spot - 50.0, 1), round(btc_spot + 50.0, 1)],
        'stop_loss': dec.dynamic_stop_loss,
        'take_profit_1': dec.dynamic_tp1,
        'take_profit_2': dec.dynamic_tp2,
        'sl_distance_usd': dec.sl_distance_usd,
        'tp1_distance_usd': dec.tp1_distance_usd,
        'risk_reward_ratio': dec.risk_reward_ratio,
        'status_description': dec.status_description,
        'timestamp_utc': time.strftime('%H:%M:%S UTC', time.gmtime()),
        
        # Multi-timeframe Indicator Data Pack for Cockpit UI
        'mtf': mtf_data,
        'mtf_summary': mtf_data.get('summary_text', ''),
        'macro_alignment': mtf_data.get('macro_alignment', 'CHOPPY_RANGE'),
        
        # Institutional Order Flow
        'open_interest_usd': inst.get('open_interest_usd', 0.0),
        'oi_usd_formatted': inst.get('oi_usd_formatted', '$8.75B'),
        'oi_5m_delta_usd': inst.get('oi_delta_5m', 0.0),
        'oi_5m_delta_formatted': inst.get('oi_5m_delta_formatted', '+$0.0M'),
        'coinbase_spot': inst.get('coinbase_spot', btc_spot),
        'coinbase_premium': inst.get('coinbase_premium', 0.0),
        'funding_rate_pct': inst.get('funding_rate_pct', 0.01),
        
        # Quant Math & Order Flow Telemetry Pack
        'hurst_exponent': getattr(dec, 'hurst_exponent', 0.50),
        'hurst_regime': getattr(dec, 'hurst_regime', 'CHOP'),
        'is_random_walk': getattr(dec, 'is_random_walk', False),
        'ou_half_life_min': getattr(dec, 'ou_half_life_min', 0.0),
        'robust_z': getattr(dec, 'robust_z', 0.0),
        'perp_cvd_15m': getattr(dec, 'perp_cvd_15m', 0.0),
        'spot_cvd_15m': getattr(dec, 'spot_cvd_15m', 0.0),
        'order_flow_bias': getattr(dec, 'order_flow_bias', 'BALANCED'),
        'divergence_alert': getattr(dec, 'divergence_alert', 'NORMAL'),
        'expected_value_usd': getattr(dec, 'expected_value_usd', 0.0),
        'ev_hurdle_passed': getattr(dec, 'ev_hurdle_passed', True),
        'kelly_risk_pct': getattr(dec, 'kelly_risk_pct', 1.5),
        
        # Global Macro & Institutional Session Telemetry
        'gold_usd': getattr(dec, 'gold_usd', macro_data.get('gold_usd', 4365.0)),
        'gold_change_24h_pct': getattr(dec, 'gold_change_24h_pct', macro_data.get('gold_change_24h_pct', 0.0)),
        'eth_btc': getattr(dec, 'eth_btc', macro_data.get('eth_btc', 0.0325)),
        'eth_btc_change_24h_pct': getattr(dec, 'eth_btc_change_24h_pct', macro_data.get('eth_btc_change_24h_pct', 0.0)),
        'sol_btc': macro_data.get('sol_btc', 0.00138),
        'sol_btc_change_24h_pct': macro_data.get('sol_btc_change_24h_pct', 0.0),
        'macro_risk_regime': getattr(dec, 'macro_risk_regime', macro_data.get('macro_risk_regime', 'NEUTRAL_BALANCED')),
        'macro_description': macro_data.get('macro_description', ''),
        'session_name': getattr(dec, 'session_name', session_data.get('session_name', 'NEW_YORK_CASH_OPEN')),
        'session_label': getattr(dec, 'session_label', session_data.get('session_label', 'NEW YORK CASH OPEN')),
        'upper_liquidity_pool': getattr(dec, 'upper_liquidity_pool', session_data.get('upper_liquidity_pool', 0.0)),
        'lower_liquidity_pool': getattr(dec, 'lower_liquidity_pool', session_data.get('lower_liquidity_pool', 0.0)),
        'magnet_status': getattr(dec, 'magnet_status', session_data.get('magnet_status', '')),
        'dist_upper_pct': session_data.get('dist_upper_pct', 0.0),
        'dist_lower_pct': session_data.get('dist_lower_pct', 0.0),
        
        # L2 Order Book & Whale Wall Telemetry
        'order_book_obi': getattr(dec, 'order_book_obi', order_book_data.get('obi_ratio', 0.0)),
        'obi_pct': order_book_data.get('obi_pct', 0.0),
        'obi_bias': order_book_data.get('obi_bias', 'BALANCED'),
        'bid_depth_m': order_book_data.get('bid_depth_m', 0.0),
        'ask_depth_m': order_book_data.get('ask_depth_m', 0.0),
        'nearest_bid_wall_price': getattr(dec, 'nearest_bid_wall_price', 0.0),
        'nearest_bid_wall_btc': getattr(dec, 'nearest_bid_wall_btc', 0.0),
        'nearest_ask_wall_price': getattr(dec, 'nearest_ask_wall_price', 0.0),
        'nearest_ask_wall_btc': getattr(dec, 'nearest_ask_wall_btc', 0.0),
        'wall_summary': order_book_data.get('wall_summary', 'Depth fluid'),
        
        # Deribit DVOL Telemetry
        'dvol_index': getattr(dec, 'dvol_index', dvol_data.get('dvol', 35.0)),
        'dvol_regime': getattr(dec, 'dvol_regime', dvol_data.get('dvol_regime', 'MODERATE_VOLATILITY')),
        'dvol_label': dvol_data.get('dvol_label', 'VOLATILITY STABLE'),
        'dvol_multiplier': getattr(dec, 'dvol_multiplier', 1.0),
        
        # Economic News Shield Telemetry
        'is_news_blackout': getattr(dec, 'is_news_blackout', news_data.get('is_blackout_active', False)),
        'news_shield_status': getattr(dec, 'news_shield_status', news_data.get('shield_status', 'Clear')),
        'upcoming_news_event': getattr(dec, 'upcoming_news_event', news_data.get('upcoming_event', 'None')),
        'blackout_reason': news_data.get('blackout_reason', ''),
        
        # Superpower Telemetry Pack
        'strategy_mode': getattr(dec, 'strategy_mode', 'TREND_EXPANSION'),
        'post_only_price': getattr(dec, 'post_only_price', btc_spot),
        'fee_savings_est_usd': getattr(dec, 'fee_savings_est_usd', 0.0),
        'cumulative_fees_saved': DIRECTIONAL_MANAGER.cumulative_fees_saved if 'DIRECTIONAL_MANAGER' in globals() and DIRECTIONAL_MANAGER else 0.0,
        'coinbase_lead_lag_bias': lead_lag_data.get('lead_lag_bias', 'BALANCED_SYNCHRONIZED'),
        'coinbase_lead_lag_label': lead_lag_data.get('lead_lag_label', 'Spot-Perp Synchronized'),
        'coinbase_velocity_delta': lead_lag_data.get('lead_lag_velocity_delta', 0.0),
        'memory_lesson': getattr(dec, 'memory_lesson', memory_data.get('lesson_summary', '')),
        
        # Liquidation Cascade & Multi-Asset Telemetry
        'upper_short_liq_price': liq_data.get('upper_short_liq_price', btc_spot * 1.015),
        'upper_short_liq_pool_m': liq_data.get('upper_short_liq_pool_m', 0.0),
        'lower_long_liq_price': liq_data.get('lower_long_liq_price', btc_spot * 0.985),
        'lower_long_liq_pool_m': liq_data.get('lower_long_liq_pool_m', 0.0),
        'liq_cascade_status': liq_data.get('cascade_status', 'LIQUIDATION_POOLS_STABLE'),
        'liq_cascade_label': liq_data.get('cascade_label', 'Liquidation Clusters Stable'),
        'top_asset_opportunity': scanner_data.get('top_opportunity', 'BTCUSDT'),
        'top_asset_score': scanner_data.get('top_score', 50.0),
        
        'engine_mode': dec.engine_mode,
    }

    if dec.signal_type != 'NEUTRAL':
        if not LAST_LONG_SHORT_CALLS or (LAST_LONG_SHORT_CALLS[0].get('signal_type') != dec.signal_type and abs(now - LAST_LONG_SHORT_CALLS[0].get('ts_raw', 0)) > 60):
            call_entry = dict(current_call)
            call_entry['ts_raw'] = now
            LAST_LONG_SHORT_CALLS.insert(0, call_entry)
            if len(LAST_LONG_SHORT_CALLS) > 25:
                LAST_LONG_SHORT_CALLS.pop()

    return current_call



class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/telemetry":
            self.send_json(CACHED_TELEMETRY)
        elif path == "/api/status":
            self.send_json(self.handle_get_status())
        elif path == "/api/jev/telemetry":
            self.send_json(self.handle_get_jev_telemetry())
        elif path == "/api/binance/universe":
            qs = urllib.parse.parse_qs(parsed.query)
            force_r = "force" in qs and qs["force"][0].lower() in ["true", "1"]
            self.send_json(GLOBAL_BINANCE_SCANNER.scan_universe(max_ranked=20, force_refresh=force_r) if GLOBAL_BINANCE_SCANNER else {"status": "loading"})
        elif path in ["/api/autotrade/status", "/api/autotrade/positions"]:
            self.send_json(GLOBAL_AUTONOMOUS_TRADER.get_status() if GLOBAL_AUTONOMOUS_TRADER else {"running": False})
        elif path == "/api/autotrade/history":
            history = []
            hist_file = RUNTIME_DIR / "autonomous_trade_history.json"
            if hist_file.exists():
                try:
                    history = json.loads(hist_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            self.send_json({"closed_trades": list(reversed(history)), "total_count": len(history)})
        elif path == "/api/binance/mode":
            self.send_json(GLOBAL_BINANCE_ADAPTER.get_status() if GLOBAL_BINANCE_ADAPTER else {"mode": "PAPER"})
        elif path == "/api/multi-asset/scan":
            self.send_json(GLOBAL_MULTI_ASSET_SCANNER.scan_all_assets() if GLOBAL_MULTI_ASSET_SCANNER else {"status": "loading"})
        elif path == "/api/sim/monte-carlo":
            self.send_json(GLOBAL_MONTE_CARLO.run_simulation() if GLOBAL_MONTE_CARLO else {"status": "loading"})
        elif path == "/api/liquidation/heatmap":
            spot = float(CACHED_TELEMETRY.get("binance", {}).get("spot", 81300.0))
            self.send_json(GLOBAL_LIQUIDATION_ENGINE.get_liquidation_state(spot) if GLOBAL_LIQUIDATION_ENGINE else {"status": "loading"})
        elif path == "/api/jev/long-short":
            self.send_json(self.handle_get_jev_long_short())
        elif path == "/api/jev/directional/position":
            self.send_json(self.handle_get_directional_position())
        elif path == "/api/jev/klines":
            self.send_json(self.handle_get_jev_klines(parsed.query))
        elif path == "/api/backtest":
            self.send_json(self.handle_get_backtest())
        elif path == "/api/logs":
            self.send_json(self.handle_get_logs())
        elif path == "/api/session-trades":
            self.send_json(self.handle_get_session_trades())
        elif path == "/api/trades":
            self.send_json(self.handle_get_trades(parsed.query))
        elif path == "/api/settings":
            self.send_json(self.handle_get_settings())
        elif path == "/api/strategy-comparison":
            self.send_json(self.handle_get_strategy_comparison())
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if path == "/api/autotrade/toggle":
            turn_on = bool(payload.get("enabled", False))
            if GLOBAL_AUTONOMOUS_TRADER:
                res = GLOBAL_AUTONOMOUS_TRADER.start() if turn_on else GLOBAL_AUTONOMOUS_TRADER.stop()
            else:
                res = {"success": False, "error": "Autopilot not loaded"}
            self.send_json(res)
        elif path == "/api/autotrade/close":
            sym = payload.get("symbol")
            if GLOBAL_AUTONOMOUS_TRADER and sym:
                if hasattr(GLOBAL_AUTONOMOUS_TRADER, "close_position"):
                    res = GLOBAL_AUTONOMOUS_TRADER.close_position(sym, reason="MANUAL_CLOSE")
                    self.send_json(res)
                elif sym in GLOBAL_AUTONOMOUS_TRADER.open_positions:
                    del GLOBAL_AUTONOMOUS_TRADER.open_positions[sym]
                    GLOBAL_AUTONOMOUS_TRADER._save_positions()
                    self.send_json({"success": True, "closed": sym})
                else:
                    self.send_json({"success": False, "error": "Position not found"})
            else:
                self.send_json({"success": False, "error": "Invalid request"})
        elif path == "/api/binance/mode":
            mode_target = payload.get("mode", "paper")
            self.send_json(GLOBAL_BINANCE_ADAPTER.set_mode(mode_target) if GLOBAL_BINANCE_ADAPTER else {"success": False})
        elif path == "/api/binance/execute":
            sym = payload.get("symbol", "SOLUSDT")
            side = payload.get("side", "BUY")
            qty = float(payload.get("quantity", 1.0))
            px = float(payload.get("price", 112.0))
            res = GLOBAL_BINANCE_ADAPTER.execute_order(sym, side, qty, px) if GLOBAL_BINANCE_ADAPTER else {"success": False}
            if res.get("success") and GLOBAL_TELEGRAM_BOT:
                sl_calc = round(px * 0.985 if side == "BUY" else px * 1.015, 4)
                tp1_calc = round(px * 1.02 if side == "BUY" else px * 0.98, 4)
                tp2_calc = round(px * 1.04 if side == "BUY" else px * 0.96, 4)
                GLOBAL_TELEGRAM_BOT.notify_entry(
                    symbol=sym,
                    side=side,
                    price=px,
                    leverage=10,
                    sl=sl_calc,
                    tp1=tp1_calc,
                    tp2=tp2_calc,
                    ev_usd=round(qty * px * 0.03, 2),
                    rationale="Binance Full Universe Alpha Breakout / Mispricing"
                )
            self.send_json(res)
        elif path == "/api/telegram/test":
            if GLOBAL_TELEGRAM_BOT:
                report = GLOBAL_TELEGRAM_BOT._build_today_report()
                res = GLOBAL_TELEGRAM_BOT.send_message(report)
            else:
                res = {"success": False, "error": "Bot uninitialized"}
            self.send_json(res)
        elif path == "/api/jev/directional/open":
            self.send_json(self.handle_directional_open(payload))
        elif path == "/api/jev/directional/close":
            self.send_json(self.handle_directional_close(payload))
        elif path == "/api/jev/directional/config":
            self.send_json(self.handle_directional_config(payload))
        elif path == "/api/jev/directional/reset":
            self.send_json(self.handle_directional_reset())
        elif path == "/api/bot/start":
            self.send_json(self.handle_bot_start(payload))
        elif path == "/api/bot/stop":
            self.send_json(self.handle_bot_stop())
        elif path == "/api/run-backtest":
            self.send_json(self.handle_trigger_backtest())
        elif path == "/api/run-multi-backtest":
            self.send_json(self.handle_trigger_multi_backtest())
        elif path == "/api/settings":
            self.send_json(self.handle_save_settings(payload))
        else:
            self.send_error(404, "Endpoint not found")

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


    def handle_get_jev_telemetry(self):
        global GLOBAL_JEV_ENGINE, LAST_JEV_DECISION, STREAM_MGR, CACHED_TELEMETRY
        vel_metrics = STREAM_MGR.get_velocity_metrics() if STREAM_MGR else {
            'velocity_15s': 0.0,
            'velocity_60s': 0.0,
            'acceleration': 0.0,
            'latency_ms': 0.5,
            'tick_count': 0,
        }

        mkt = CACHED_TELEMETRY.get('market', {})
        clob_up = mkt.get('clob_up', {})
        clob_down = mkt.get('clob_down', {})
        
        up_bid = clob_up.get('bid')
        up_ask = clob_up.get('ask')
        dn_bid = clob_down.get('bid')
        dn_ask = clob_down.get('ask')
        spread_up = clob_up.get('spread')
        spread_down = clob_down.get('spread')
        skew_up = mkt.get('skew_up_pct', 50.0)
        skew_down = mkt.get('skew_down_pct', 50.0)
        
        btc_telemetry = CACHED_TELEMETRY.get('binance', {})
        btc_spot = btc_telemetry.get('spot', 0.0)
        btc_open = btc_telemetry.get('open', 0.0)
        impulse_5m = btc_telemetry.get('impulse', 0.0)
        
        now = time.time()
        cur_5m = int(now // 300) * 300
        sec_rem = max(0, int(cur_5m + 300 - now))
        if STREAM_MGR:
            s_spot, s_open, s_imp = STREAM_MGR.get_btc_telemetry(cur_5m)
            if s_spot:
                btc_spot = s_spot
            if s_open:
                btc_open = s_open
            if s_imp is not None:
                impulse_5m = s_imp
        
        now_ts = time.time()
        last_eval_time = LAST_JEV_DECISION.get('timestamp', 0)
        
        # If Jev engine is ready and market data exists, periodically evaluate
        if GLOBAL_JEV_ENGINE and MarketStatePayload:
            # Re-evaluate every 3.5 seconds or if no decision yet
            if (now_ts - last_eval_time > 3.5 or not LAST_JEV_DECISION.get('decision')):
                try:
                    payload = MarketStatePayload(
                        slug=mkt.get('slug', 'btc-5m'),
                        seconds_left=sec_rem,
                        btc_spot=float(btc_spot or 0.0),
                        btc_open=float(btc_open or 0.0),
                        btc_impulse=float(impulse_5m or 0.0),
                        up_ask=up_ask,
                        up_bid=up_bid,
                        dn_ask=dn_ask,
                        dn_bid=dn_bid,
                        spread=spread_up,
                        velocity_15s=float(vel_metrics.get('velocity_15s', 0.0)),
                        velocity_60s=float(vel_metrics.get('velocity_60s', 0.0)),
                        acceleration=float(vel_metrics.get('acceleration', 0.0)),
                        max_entry_price=0.65,
                        min_entry_seconds_left=30,
                        max_entry_seconds_left=270,
                        base_stake=5.0,
                        max_stake=10.0
                    )
                    dec_res = GLOBAL_JEV_ENGINE.evaluate_state(payload)
                    import dataclasses
                    LAST_JEV_DECISION['timestamp'] = now_ts
                    LAST_JEV_DECISION['decision'] = dataclasses.asdict(dec_res)
                except Exception as e:
                    pass

        with BOT_PROCESS_LOCK:
            legacy_running = BOT_PROCESS is not None and BOT_PROCESS.poll() is None
            autotrade_running = bool(GLOBAL_AUTONOMOUS_TRADER and getattr(GLOBAL_AUTONOMOUS_TRADER, "is_running", False))
            is_running = legacy_running or autotrade_running
            bot_info = dict(CURRENT_BOT_INFO)
            if autotrade_running and GLOBAL_AUTONOMOUS_TRADER:
                bot_info["autopilot"] = GLOBAL_AUTONOMOUS_TRADER.get_status()

        inst = INSTITUTIONAL_CACHE or {}
        inst_oi_usd = inst.get("open_interest_usd", 0.0)
        inst_oi_delta = inst.get("oi_delta_5m", 0.0)
        inst_cb_prem = inst.get("coinbase_premium", 0.0)
        inst_funding = inst.get("funding_rate_pct", 0.0)
        inst_oi_fmt = inst.get("oi_usd_formatted", "$0.00B")
        inst_delta_fmt = inst.get("oi_5m_delta_formatted", "+$0.0M")

        return {
            'status': 'ok',
            'jev_ready': GLOBAL_JEV_ENGINE is not None and getattr(GLOBAL_JEV_ENGINE, 'is_live_ready', lambda: False)(),
            'bot_running': is_running,
            'bot_info': bot_info,
            'market': {
                'btc_spot': btc_spot,
                'btc_open': btc_open,
                'btc_impulse': impulse_5m,
                'up_bid': up_bid,
                'up_ask': up_ask,
                'dn_bid': dn_bid,
                'dn_ask': dn_ask,
                'spread_up': spread_up,
                'spread_down': spread_down,
                'skew_up': skew_up,
                'skew_down': skew_down,
                'seconds_left': sec_rem,
                'slug': mkt.get('slug', 'btc-5m'),
            },
            'velocity': vel_metrics,
            'institutional': {
                'oi_usd': inst_oi_usd,
                'oi_usd_formatted': inst_oi_fmt,
                'oi_5m_delta_formatted': inst_delta_fmt,
                'oi_delta_5m': inst_oi_delta,
                'coinbase_premium': inst_cb_prem,
                'funding_rate_pct': inst_funding,
            },
            'decision': LAST_JEV_DECISION.get('decision'),
            'server_time': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
        }


    def handle_get_jev_long_short(self):
        global LAST_LONG_SHORT_CALLS
        current_call = compute_current_long_short_call()
        return {
            'status': 'ok',
            'current_call': current_call,
            'recent_history': LAST_LONG_SHORT_CALLS,
            'server_time': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
        }

    def handle_get_directional_position(self):
        global DIRECTIONAL_MANAGER
        return DIRECTIONAL_MANAGER.get_state()

    def handle_directional_open(self, payload):
        global DIRECTIONAL_MANAGER, CACHED_TELEMETRY, STREAM_MGR
        side = str(payload.get("side", "LONG")).upper()
        leverage = int(payload.get("leverage", 20))
        risk_pct = float(payload.get("risk_pct", 2.0))
        balance = float(payload.get("balance")) if payload.get("balance") else None
        custom_sl = float(payload.get("stop_loss")) if payload.get("stop_loss") else None
        custom_tp1 = float(payload.get("tp1")) if payload.get("tp1") else None
        custom_tp2 = float(payload.get("tp2")) if payload.get("tp2") else None
        rationale = payload.get("rationale", "")

        import time
        now = time.time()
        cur_5m = int(now // 300) * 300
        spot = 0.0
        if STREAM_MGR:
            s_spot, _, _ = STREAM_MGR.get_btc_telemetry(cur_5m)
            if s_spot: spot = s_spot
        if not spot:
            spot = float(payload.get("entry_price") or CACHED_TELEMETRY.get("binance", {}).get("spot", 81250.0))

        return DIRECTIONAL_MANAGER.open_position(
            side=side,
            entry_price=spot,
            leverage=leverage,
            risk_pct=risk_pct,
            balance=balance,
            custom_sl=custom_sl,
            custom_tp1=custom_tp1,
            custom_tp2=custom_tp2,
            rationale=rationale
        )

    def handle_directional_close(self, payload):
        global DIRECTIONAL_MANAGER, CACHED_TELEMETRY, STREAM_MGR
        import time
        now = time.time()
        cur_5m = int(now // 300) * 300
        spot = 0.0
        if STREAM_MGR:
            s_spot, _, _ = STREAM_MGR.get_btc_telemetry(cur_5m)
            if s_spot: spot = s_spot
        if not spot:
            spot = float(payload.get("exit_price") or CACHED_TELEMETRY.get("binance", {}).get("spot", 81250.0))

        reason = payload.get("reason", "USER_MARKET_EXIT")
        return DIRECTIONAL_MANAGER.close_position(spot, reason=reason)

    def handle_directional_config(self, payload):
        global DIRECTIONAL_MANAGER
        DIRECTIONAL_MANAGER.update_config(payload)
        return {"success": True, "bot_config": DIRECTIONAL_MANAGER.bot_config}

    def handle_directional_reset(self):
        global DIRECTIONAL_MANAGER
        DIRECTIONAL_MANAGER.reset_account()
        return {"success": True, "state": DIRECTIONAL_MANAGER.get_state()}


    def handle_get_jev_klines(self, query_string: str = ""):
        global GLOBAL_KLINE_CACHE
        import time, requests, urllib.parse
        parsed = urllib.parse.parse_qs(query_string)
        symbol = parsed.get("symbol", ["BTCUSDT"])[0].upper().replace("/", "").replace(" ", "")
        interval = parsed.get("interval", ["5m"])[0]
        limit = min(100, max(10, int(parsed.get("limit", ["40"])[0])))
        cache_key = f"{symbol}_{interval}_{limit}"
        now = time.time()

        if cache_key in GLOBAL_KLINE_CACHE:
            cached_ts, cached_candles = GLOBAL_KLINE_CACHE[cache_key]
            if (now - cached_ts) < 2.0:
                return {"status": "ok", "symbol": symbol, "interval": interval, "candles": cached_candles}

        # Select endpoints: XAUUSDT and futures use fapi.binance.com, spot uses api.binance.com
        urls_to_try = []
        if symbol == "XAUUSDT":
            urls_to_try = [
                ("https://fapi.binance.com/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}),
                ("https://api.binance.com/api/v3/klines", {"symbol": "PAXGUSDT", "interval": interval, "limit": limit}),
            ]
        else:
            urls_to_try = [
                ("https://api.binance.com/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit}),
                ("https://fapi.binance.com/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}),
            ]

        for url, params in urls_to_try:
            try:
                r = requests.get(url, params=params, timeout=3)
                if r.status_code == 200:
                    raw = r.json()
                    candles = []
                    for k in raw:
                        candles.append({
                            "time": int(k[0]),
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "volume": float(k[5])
                        })
                    GLOBAL_KLINE_CACHE[cache_key] = (now, candles)
                    return {"status": "ok", "symbol": symbol, "interval": interval, "candles": candles}
            except Exception:
                continue

        # Fallback to cached or error
        if cache_key in GLOBAL_KLINE_CACHE:
            return {"status": "ok", "symbol": symbol, "interval": interval, "candles": GLOBAL_KLINE_CACHE[cache_key][1]}
        return {"status": "error", "message": f"Failed to fetch klines for {symbol} from Binance."}

    def handle_get_status(self):
        global BOT_PROCESS, CURRENT_BOT_INFO, GLOBAL_AUTONOMOUS_TRADER
        with BOT_PROCESS_LOCK:
            legacy_running = BOT_PROCESS is not None and BOT_PROCESS.poll() is None
            autotrade_running = bool(GLOBAL_AUTONOMOUS_TRADER and getattr(GLOBAL_AUTONOMOUS_TRADER, "is_running", False))
            CURRENT_BOT_INFO["running"] = legacy_running or autotrade_running
            if autotrade_running and GLOBAL_AUTONOMOUS_TRADER:
                CURRENT_BOT_INFO["autopilot"] = GLOBAL_AUTONOMOUS_TRADER.get_status()
            elif not legacy_running:
                CURRENT_BOT_INFO["pid"] = None

        return {
            "bot": CURRENT_BOT_INFO,
            "telemetry": CACHED_TELEMETRY,
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

    def handle_get_backtest(self):
        json_path = REPORTS_DIR / "backtest_sunday_20260830_conservative.json"
        if not json_path.exists():
            return {"error": "Backtest report not found. Please trigger a backtest."}
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"error": str(e)}

    def handle_get_logs(self):
        log_files = sorted(RUNTIME_DIR.glob("*.log"), key=os.path.getmtime, reverse=True)
        if not log_files:
            return {"logs": "No bot runtime logs found in runtime/ directory."}
        try:
            target_file = log_files[0]
            if CURRENT_BOT_INFO.get("log_file"):
                p = RUNTIME_DIR / CURRENT_BOT_INFO["log_file"]
                if p.exists():
                    target_file = p
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                return {
                    "file": target_file.name,
                    "logs": "".join(lines[-150:]),
                }
        except Exception as e:
            return {"logs": f"Error reading logs: {e}"}

    def handle_get_session_trades(self):
        """Parse completed trades from runtime log files for session history."""
        trades = []
        log_files = sorted(RUNTIME_DIR.glob("*.log"), key=os.path.getmtime, reverse=True)[:5]
        for lf in log_files:
            try:
                txt = open(lf, "r", encoding="utf-8", errors="ignore").read()
                # Find JSON objects at the end of the log
                import re
                matches = re.findall(r'(\{(?:[^{}]|(?R))*\})', txt) if hasattr(re, 'VERBOSE') else []
                # Simple parser
                idx = txt.rfind('\n{')
                if idx != -1:
                    raw = txt[idx+1:]
                    obj = json.loads(raw)
                    opened = obj.get("opened") or {}
                    closed = obj.get("closed") or {}
                    pnl = obj.get("realized_cashflow_pnl_usdc")
                    if opened.get("side"):
                        trades.append({
                            "log": lf.name,
                            "market": opened.get("market_slug"),
                            "side": opened.get("side"),
                            "entry_price": opened.get("entry_price"),
                            "stake_usd": opened.get("cost_usdc"),
                            "close_reason": closed.get("close_reason"),
                            "close_price": closed.get("close_usdc"),
                            "pnl": pnl,
                            "time": obj.get("started_at"),
                        })
            except Exception:
                pass
        return {"trades": trades}

    def handle_get_trades(self, query_string: str = ""):
        parsed_q = urllib.parse.parse_qs(query_string)
        raw_mode = (parsed_q.get("mode", ["paper"])[0]).lower()
        is_live = raw_mode in ("live", "execute", "real")
        mode = "live" if is_live else "paper"
        filename = "trades_live.json" if is_live else "trades_paper.json"
        target_path = RUNTIME_DIR / filename

        # Auto-seed trades_paper.json if missing or empty
        if not is_live and (not target_path.exists() or target_path.stat().st_size < 5):
            self.seed_paper_trades_from_logs(target_path)

        trades = []
        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        trades = data
            except Exception:
                trades = []

        # Sort trades by timestamp descending (newest first)
        trades.sort(key=lambda t: str(t.get("timestamp") or ""), reverse=True)

        total_trades = len(trades)
        wins = sum(1 for t in trades if float(t.get("pnl_usdc") or 0) > 0)
        losses = sum(1 for t in trades if float(t.get("pnl_usdc") or 0) <= 0)
        win_rate = round((wins / total_trades) * 100, 1) if total_trades > 0 else 0.0
        net_pnl = round(sum(float(t.get("pnl_usdc") or 0) for t in trades), 4)
        avg_pnl = round(net_pnl / total_trades, 4) if total_trades > 0 else 0.0
        gross_win = sum(float(t.get("pnl_usdc") or 0) for t in trades if float(t.get("pnl_usdc") or 0) > 0)
        gross_loss = abs(sum(float(t.get("pnl_usdc") or 0) for t in trades if float(t.get("pnl_usdc") or 0) < 0))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

        return {
            "mode": mode,
            "stats": {
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate_pct": win_rate,
                "net_pnl_usdc": net_pnl,
                "avg_pnl_usdc": avg_pnl,
                "profit_factor": profit_factor,
            },
            "trades": trades,
        }

    def seed_paper_trades_from_logs(self, target_path: Path):
        """Parse prior completed trades from log files and seed trades_paper.json."""
        log_files = sorted(RUNTIME_DIR.glob("*.log"), key=os.path.getmtime)
        seeded = []
        seen_keys = set()
        for lf in log_files:
            try:
                txt = open(lf, "r", encoding="utf-8", errors="ignore").read()
                idx = txt.rfind('\n{')
                if idx != -1:
                    raw = txt[idx+1:].strip()
                    report = json.loads(raw)
                    opened = report.get("opened") or {}
                    closed = report.get("closed") or {}
                    pnl = report.get("realized_cashflow_pnl_usdc")
                    if opened and closed and pnl is not None:
                        ts = closed.get("closed_at") or report.get("finished_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(lf)))
                        market = opened.get("market_slug", "")
                        side = opened.get("side", "UP")
                        uniq_key = f"{market}_{ts}_{side}"
                        if uniq_key in seen_keys:
                            continue
                        seen_keys.add(uniq_key)
                        
                        entry_p = float(opened.get("entry_price") or 0.70)
                        shares = float(opened.get("shares") or 0.0)
                        cost = float(opened.get("cost_usdc") or 5.0)
                        close_usdc = float(closed.get("close_usdc") or 0.0)
                        exit_p = round(close_usdc / shares, 4) if shares > 0 else 0.0
                        pnl_f = float(pnl)
                        pnl_pct = round((pnl_f / cost) * 100, 2) if cost > 0 else 0.0

                        seeded.append({
                            "id": f"seed_{len(seeded)+1}_{lf.stem[-6:]}",
                            "timestamp": ts,
                            "mode": "paper",
                            "market_slug": market,
                            "side": side,
                            "entry_price": entry_p,
                            "shares": shares,
                            "cost_usdc": cost,
                            "exit_price": exit_p,
                            "exit_usdc": close_usdc,
                            "exit_reason": closed.get("close_reason") or "settlement",
                            "pnl_usdc": pnl_f,
                            "pnl_pct": pnl_pct,
                            "status": "WIN" if pnl_f > 0 else "LOSS",
                            "order_id": opened.get("open_order_id") or f"sim_open_{lf.stem}",
                            "close_order_id": closed.get("close_order_id") or f"sim_close_{lf.stem}",
                            "tx_hash": opened.get("open_tx") or "sim_tx_seed",
                            "close_tx": closed.get("close_tx") or "sim_tx_seed",
                        })
            except Exception:
                pass
        if seeded:
            try:
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(seeded, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def handle_get_settings(self):
        cfg_path = PROJECT_ROOT / "config" / "btc_5m_profiles.yaml"
        if not cfg_path.exists():
            return {"error": "Config file not found"}
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return {"success": True, "config": data}
        except Exception as e:
            return {"error": str(e)}

    def handle_save_settings(self, payload):
        cfg_path = PROJECT_ROOT / "config" / "btc_5m_profiles.yaml"
        if not cfg_path.exists():
            return {"error": "Config file not found"}
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # Update profile settings if provided
            profiles = data.get("profiles", {})
            all_profile_names = [
                "jev_ai_brain",
                "momentum_value", "quick_scalp", "mean_reversion",
                "skew_hedge", "macro_trend_sniper", "conservative", "aggressive"
            ]
            for prof_name in all_profile_names:
                if prof_name in payload:
                    prof_update = payload[prof_name]
                    if prof_name not in profiles:
                        profiles[prof_name] = {}
                    if "threshold_price" in prof_update:
                        profiles[prof_name].setdefault("signal", {})["threshold_price"] = float(prof_update["threshold_price"])
                    if "max_entry_price" in prof_update:
                        profiles[prof_name].setdefault("signal", {})["max_entry_price"] = float(prof_update["max_entry_price"])
                    if "min_btc_impulse" in prof_update:
                        profiles[prof_name].setdefault("signal", {})["min_btc_impulse"] = float(prof_update["min_btc_impulse"])
                    if "stake_usd" in prof_update:
                        profiles[prof_name].setdefault("sizing", {})["stake_usd"] = float(prof_update["stake_usd"])
                    if "stop_loss_pct_from_entry" in prof_update:
                        profiles[prof_name].setdefault("stop_loss", {})["stop_loss_pct_from_entry"] = float(prof_update["stop_loss_pct_from_entry"])
                    if "take_profit_pct" in prof_update:
                        profiles[prof_name].setdefault("take_profit", {})["take_profit_pct"] = float(prof_update["take_profit_pct"])
                        profiles[prof_name]["take_profit"]["enabled"] = True
                    if "trailing_stop_pct" in prof_update:
                        profiles[prof_name].setdefault("trailing_stop", {})["trailing_stop_pct"] = float(prof_update["trailing_stop_pct"])
                        profiles[prof_name]["trailing_stop"]["enabled"] = True
                    if "max_trades_per_day" in prof_update:
                        profiles[prof_name].setdefault("sizing", {})["max_trades_per_day"] = int(prof_update["max_trades_per_day"])
                    if "exit_before_sec" in prof_update:
                        profiles[prof_name].setdefault("timing", {})["exit_before_sec"] = int(prof_update["exit_before_sec"])

            if "shared_rules" in payload:
                for k, v in payload["shared_rules"].items():
                    data.setdefault("shared_rules", {})[k] = v

            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)

            return {"success": True, "message": "Settings saved successfully", "config": data}
        except Exception as e:
            return {"error": str(e)}

    def handle_bot_start(self, payload):
        global BOT_PROCESS, CURRENT_BOT_INFO
        with BOT_PROCESS_LOCK:
            if BOT_PROCESS is not None and BOT_PROCESS.poll() is None:
                return {"success": False, "status": "error", "message": "Bot is already running."}

            profile = payload.get("profile", "jev_ai_brain")
            execute = bool(payload.get("execute", False)) or (payload.get("mode") == "real")
            stake_usd = float(payload.get("stake_usd") or payload.get("stake") or 5.0)

            # Confidence & Reversal risk thresholds
            min_conf = float(payload.get("min_conf") or payload.get("threshold", 65))
            threshold = min_conf / 100.0 if min_conf > 1.0 else min_conf

            max_trap = float(payload.get("max_trap", 35))
            max_reversal = max_trap / 100.0 if max_trap > 1.0 else max_trap

            run_mode = payload.get("run_mode", "continuous_loop")

            venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
            python_exe = str(venv_py) if venv_py.exists() else sys.executable
            script_path = PROJECT_ROOT / "scripts" / "test_btc_5m_session_exit_sl.py"

            cmd = [
                python_exe,
                str(script_path),
                "--profile", profile,
                "--stake-usd", str(stake_usd),
                "--poll-sec", "2",
            ]
            if execute:
                cmd.append("--execute")
            if profile == "jev_ai_brain" or payload.get("use_jev"):
                cmd.extend([
                    "--use-jev",
                    "--jev-min-confidence", str(threshold),
                    "--jev-max-reversal", str(max_reversal),
                ])
            else:
                cmd.extend(["--threshold", str(threshold)])

            if run_mode == "continuous_loop":
                cmd.extend(["--loop", "--entry-timeout-min", "1440"])
                if "max_trades" in payload and payload["max_trades"]:
                    cmd.extend(["--max-trades", str(int(payload["max_trades"]))])
            else:
                cmd.extend(["--entry-timeout-min", "35"])

            log_file = RUNTIME_DIR / f"bot_{profile}_{int(time.time())}.log"
            out_f = open(log_file, "w", encoding="utf-8")

            try:
                BOT_PROCESS = subprocess.Popen(
                    cmd,
                    stdout=out_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT),
                )
                CURRENT_BOT_INFO = {
                    "running": True,
                    "pid": BOT_PROCESS.pid,
                    "profile": profile,
                    "mode": "execute" if execute else "paper",
                    "stake_usd": stake_usd,
                    "threshold": threshold,
                    "run_mode": run_mode,
                    "is_loop": (run_mode == "continuous_loop"),
                    "started_at": time.strftime("%H:%M:%S UTC", time.gmtime()),
                    "log_file": log_file.name,
                }
                return {"success": True, "status": "ok", "info": CURRENT_BOT_INFO}
            except Exception as e:
                return {"success": False, "status": "error", "error": str(e), "message": str(e)}

    def handle_bot_stop(self):
        global BOT_PROCESS, CURRENT_BOT_INFO
        with BOT_PROCESS_LOCK:
            if BOT_PROCESS is None or BOT_PROCESS.poll() is not None:
                CURRENT_BOT_INFO["running"] = False
                return {"success": True, "status": "ok", "message": "Bot is not running."}

            try:
                BOT_PROCESS.terminate()
                BOT_PROCESS.wait(timeout=3)
            except Exception:
                try:
                    BOT_PROCESS.kill()
                except Exception:
                    pass

            BOT_PROCESS = None
            CURRENT_BOT_INFO["running"] = False
            CURRENT_BOT_INFO["pid"] = None
            return {"success": True, "status": "ok", "message": "Bot process stopped cleanly."}

    def handle_trigger_backtest(self):
        python_exe = sys.executable
        script = PROJECT_ROOT / "scripts" / "backtest_24h_sunday.py"
        threading.Thread(
            target=lambda: subprocess.run([python_exe, str(script)], cwd=str(PROJECT_ROOT)),
            daemon=True,
        ).start()
        return {"success": True, "message": "Backtest started in background."}

    def handle_get_strategy_comparison(self):
        json_path = REPORTS_DIR / "multi_strategy_comparison.json"
        if not json_path.exists():
            return {"error": "Multi-strategy backtest report not found. Please run backtest first."}
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"error": str(e)}

    def handle_trigger_multi_backtest(self):
        python_exe = sys.executable
        script = PROJECT_ROOT / "scripts" / "backtest_multi_strategy.py"
        threading.Thread(
            target=lambda: subprocess.run([python_exe, str(script)], cwd=str(PROJECT_ROOT)),
            daemon=True,
        ).start()
        return {"success": True, "message": "Multi-strategy backtest started in background."}


def run_server(port=5000):
    # Start background telemetry poller
    t = threading.Thread(target=telemetry_background_worker, daemon=True)
    t.start()
    t_inst = threading.Thread(target=institutional_telemetry_worker, daemon=True)
    t_inst.start()

    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"\n=======================================================")
    print(f"  BTC 5M Polymarket Trading Cockpit Server Active")
    print(f"  --> URL: http://localhost:{port}")
    print(f"=======================================================\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()


if __name__ == "__main__":
    p = 5000
    if len(sys.argv) > 2 and sys.argv[1] == "--port":
        p = int(sys.argv[2])
    run_server(p)